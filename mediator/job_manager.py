
"""
Estado del job activo.

Como hay un solo cliente a la vez (uso personal), el mediador mantiene
como mucho UN job "en curso" — no hay cola, y esto vale para los dos
"kind" que existen hoy (transcribe/translate) por igual: no tiene
sentido transcribir y traducir al mismo tiempo en una tarjeta de 4GB.
Este módulo es el único punto de verdad sobre ese job, sea cual sea su
kind.

Cada kind tiene su propio model manager (whisper para "transcribe",
llama-cpp para "translate" — ver mediator/model_manager.py y
mediator/translation_model_manager.py), inyectados por kind en el
constructor. finish()/force_release()/el watchdog liberan el manager
que corresponda según job.kind — nunca los dos a la vez, porque nunca
hay más de un job activo.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from . import config, logging_setup


@dataclass
class ReceivedFile:
    index: int
    filename: str
    duration: float
    path: Path


@dataclass
class Job:
    job_id: str
    number: int
    kind: str  # "transcribe" | "translate"
    config: dict[str, Any]
    files_expected: int
    tmp_dir: Path
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    # transcribe: awaiting_files -> loading_model -> processing -> done | failed
    # translate:  loading_model -> processing -> done | failed (nunca
    #             pasa por awaiting_files, no hay archivos que subir)
    state: str = "awaiting_files"
    files: list[ReceivedFile] = field(default_factory=list)
    websocket: WebSocket | None = None
    outbound: asyncio.Queue = field(default_factory=asyncio.Queue)
    cancelled: bool = False

    def touch(self) -> None:
        self.last_activity = datetime.now()

    @property
    def grace_deadline(self) -> datetime:
        return self.last_activity + timedelta(seconds=config.GRACE_PERIOD_SECONDS)


class BusyError(Exception):
    pass


class JobManager:
    """
    Recibe un model manager por kind (dict), para poder liberar el que
    corresponda cuando un job termina, se cancela, o queda abandonado.
    """

    def __init__(self, model_managers: dict[str, Any]) -> None:
        self._model_managers = model_managers
        self._job: Job | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def current(self) -> Job | None:
        return self._job

    def status(self) -> dict:
        if self._job is None:
            return {"state": "idle", "model": None, "job_id": None}
        if self._job.state in ("awaiting_files", "loading_model"):
            state = "loading"
        else:
            state = "busy"
        model_manager = self._model_managers.get(self._job.kind)
        model = getattr(model_manager, "loaded_model_size", None) if model_manager else None
        return {"state": state, "model": model, "job_id": self._job.job_id}

    # ── ciclo de vida ────────────────────────────────────────────────

    async def create_job(self, kind: str, job_config: dict, files_expected: int) -> Job:
        async with self._lock:
            if self._job is not None:
                raise BusyError(self._busy_reason())
            job_id = uuid.uuid4().hex[:8]
            number = logging_setup.next_job_number()
            tmp_dir = Path(tempfile.mkdtemp(prefix=f"mediator_job_{job_id}_"))
            job = Job(
                job_id=job_id,
                number=number,
                kind=kind,
                config=job_config,
                files_expected=files_expected,
                tmp_dir=tmp_dir,
                state="awaiting_files" if files_expected else "loading_model",
            )
            self._job = job
            files_note = f", {files_expected} archivo(s) esperado(s)" if files_expected else ""
            logging_setup.log_event(
                kind,
                f"Job #{number} aceptado — "
                f"{self._describe_config(kind, job_config)}{files_note}",
            )
            if self._watchdog_task is None or self._watchdog_task.done():
                self._watchdog_task = asyncio.create_task(self._watchdog())
            return job

    @staticmethod
    def _describe_config(kind: str, job_config: dict) -> str:
        if kind == "transcribe":
            return (
                f"modelo {job_config['model_size']}, device {job_config['device']}, "
                f"compute_type {job_config['compute_type']}"
            )
        if kind == "translate":
            return (
                f"modelo {job_config['model']}, n_gpu_layers {job_config['n_gpu_layers']}, "
                f"n_ctx {job_config['n_ctx']}"
            )
        return str(job_config)

    def _busy_reason(self) -> str:
        j = self._job
        assert j is not None
        return (
            f"Ya hay un job en curso (Job #{j.number}, kind '{j.kind}', "
            f"estado '{j.state}'). Si crees que quedó colgado, podés forzar "
            f"su liberación con DELETE /model."
        )

    def get_for(self, job_id: str) -> Job:
        if self._job is None or self._job.job_id != job_id:
            raise KeyError(job_id)
        return self._job

    async def receive_file(
        self, job_id: str, filename: str, duration: float, content: bytes,
    ) -> ReceivedFile:
        job = self.get_for(job_id)
        if job.state != "awaiting_files":
            raise ValueError(f"El job no acepta archivos en estado '{job.state}'")

        index = len(job.files)
        safe_name = Path(filename).name  # evita path traversal
        dest = job.tmp_dir / f"{index:03d}_{safe_name}"
        dest.write_bytes(content)

        rf = ReceivedFile(index=index, filename=safe_name, duration=duration, path=dest)
        job.files.append(rf)
        job.touch()

        logging_setup.log_event(
            job.kind,
            f"Job #{job.number} — archivo recibido: {safe_name} "
            f"({len(content) / (1024 * 1024):.1f} MB)",
        )
        await job.outbound.put({
            "type": "file_received",
            "file_index": index,
            "filename": safe_name,
            "files_received": len(job.files),
            "files_expected": job.files_expected,
        })
        return rf

    def all_files_received(self, job_id: str) -> bool:
        job = self.get_for(job_id)
        return len(job.files) >= job.files_expected

    def attach_ws(self, job_id: str, websocket: WebSocket) -> Job:
        job = self.get_for(job_id)
        job.websocket = websocket
        job.touch()
        return job

    def detach_ws(self, job_id: str) -> None:
        try:
            job = self.get_for(job_id)
        except KeyError:
            return
        job.websocket = None
        logging_setup.log_event(
            job.kind,
            f"Job #{job.number} — WebSocket desconectado, ventana de gracia "
            f"hasta las {job.grace_deadline.strftime('%H:%M:%S')}",
            level=logging.WARNING,
        )

    def touch(self, job_id: str) -> None:
        try:
            self.get_for(job_id).touch()
        except KeyError:
            pass

    def request_cancel(self, job_id: str) -> None:
        """
        Marca el job para que se corte entre archivos (o a mitad del
        archivo que esté procesando en ese momento) — lo chequea
        transcribe_worker.py, no acá. No tiene equivalente para
        "translate": ahí no hay loop autónomo del lado del mediador que
        interrumpir (ver translate_worker.py) — el cliente cancela
        simplemente cerrando la sesión.
        """
        try:
            job = self.get_for(job_id)
        except KeyError:
            return
        job.cancelled = True
        job.touch()
        logging_setup.log_event(
            job.kind,
            f"Job #{job.number} — cancelación solicitada por el cliente",
            level=logging.WARNING,
        )

    async def finish(self, job_id: str) -> None:
        """Job terminado de forma normal (con éxito o con una falla ya
        reportada) — libera el modelo de inmediato, sin esperar el
        timeout de zombie."""
        try:
            job = self.get_for(job_id)
        except KeyError:
            return
        model_manager = self._model_managers.get(job.kind)
        if model_manager is not None:
            model_manager.unload()
        logging_setup.log_event(job.kind, f"Job #{job.number} — modelo liberado")
        self._cleanup(job)
        if self._job is job:
            self._job = None

    async def force_release(self) -> str:
        """DELETE /model — libera lo que haya, sin importar el estado
        ni el kind. Si por algún motivo quedó algo cargado de un kind
        que ya no es el del job activo (no debería pasar, pero es
        gratis cubrirlo), se liberan los dos managers igual."""
        job = self._job
        for model_manager in self._model_managers.values():
            model_manager.unload()
        if job is not None:
            logging_setup.log_event(
                job.kind,
                f"Job #{job.number} — liberación forzada por el usuario",
                level=logging.WARNING,
            )
            self._cleanup(job)
            self._job = None
            return f"Job #{job.number} liberado."
        logging_setup.log_event(
            "server", "DELETE /model sin ningún job activo — nada que liberar",
        )
        return "No había ningún modelo cargado."

    def _cleanup(self, job: Job) -> None:
        shutil.rmtree(job.tmp_dir, ignore_errors=True)

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(config.WATCHDOG_INTERVAL_SECONDS)
            job = self._job
            if job is None or job.state in ("done", "failed"):
                continue
            if datetime.now() > job.grace_deadline:
                logging_setup.log_event(
                    job.kind,
                    f"Job #{job.number} abandonado — venció la ventana de "
                    f"gracia sin actividad",
                    level=logging.ERROR,
                )
                model_manager = self._model_managers.get(job.kind)
                if model_manager is not None:
                    model_manager.unload()
                self._cleanup(job)
                if self._job is job:
                    self._job = None
