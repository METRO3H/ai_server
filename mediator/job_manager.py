"""
Estado del job activo.

Como hay un solo cliente a la vez (uso personal), el mediador mantiene
como mucho UN job "en curso" — no hay cola. Este módulo es el único
punto de verdad sobre ese job.
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
    config: dict[str, Any]
    files_expected: int
    tmp_dir: Path
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    # awaiting_files -> loading_model -> processing -> done | failed
    state: str = "awaiting_files"
    files: list[ReceivedFile] = field(default_factory=list)
    websocket: WebSocket | None = None
    outbound: asyncio.Queue = field(default_factory=asyncio.Queue)

    def touch(self) -> None:
        self.last_activity = datetime.now()

    @property
    def grace_deadline(self) -> datetime:
        return self.last_activity + timedelta(seconds=config.GRACE_PERIOD_SECONDS)


class BusyError(Exception):
    pass


class JobManager:
    """
    Recibe el ModelManager por dependencia, para poder liberar el
    modelo cuando un job termina, se cancela, o queda abandonado.
    """

    def __init__(self, model_manager) -> None:
        self._model_manager = model_manager
        self._job: Job | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def current(self) -> Job | None:
        return self._job

    def status(self) -> dict:
        if self._job is None:
            state = "idle"
        elif self._job.state in ("awaiting_files", "loading_model"):
            state = "loading"
        else:
            state = "busy"
        return {
            "state": state,
            "model": self._model_manager.loaded_model_size,
            "job_id": self._job.job_id if self._job else None,
        }

    # ── ciclo de vida ────────────────────────────────────────────────

    async def create_job(self, job_config: dict, files_expected: int) -> Job:
        async with self._lock:
            if self._job is not None:
                raise BusyError(self._busy_reason())
            job_id = uuid.uuid4().hex[:8]
            number = logging_setup.next_job_number()
            tmp_dir = Path(tempfile.mkdtemp(prefix=f"mediator_job_{job_id}_"))
            job = Job(
                job_id=job_id,
                number=number,
                config=job_config,
                files_expected=files_expected,
                tmp_dir=tmp_dir,
            )
            self._job = job
            logging_setup.log_event(
                "transcribe",
                f"Job #{number} aceptado — modelo {job_config['model_size']}, "
                f"device {job_config['device']}, compute_type {job_config['compute_type']}, "
                f"{files_expected} archivo(s) esperado(s)",
            )
            if self._watchdog_task is None or self._watchdog_task.done():
                self._watchdog_task = asyncio.create_task(self._watchdog())
            return job

    def _busy_reason(self) -> str:
        j = self._job
        assert j is not None
        return (
            f"Ya hay un job en curso (Job #{j.number}, estado '{j.state}'). "
            f"Si crees que quedó colgado, podés forzar su liberación con DELETE /model."
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
            "transcribe",
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
            "transcribe",
            f"Job #{job.number} — WebSocket desconectado, ventana de gracia "
            f"hasta las {job.grace_deadline.strftime('%H:%M:%S')}",
            level=logging.WARNING,
        )

    def touch(self, job_id: str) -> None:
        try:
            self.get_for(job_id).touch()
        except KeyError:
            pass

    async def finish(self, job_id: str) -> None:
        """Job terminado de forma normal (con éxito o con una falla ya
        reportada) — libera el modelo de inmediato, sin esperar el
        timeout de zombie."""
        try:
            job = self.get_for(job_id)
        except KeyError:
            return
        self._model_manager.unload()
        logging_setup.log_event("transcribe", f"Job #{job.number} — modelo liberado")
        self._cleanup(job)
        if self._job is job:
            self._job = None

    async def force_release(self) -> str:
        """DELETE /model — libera lo que haya, sin importar el estado."""
        job = self._job
        self._model_manager.unload()
        if job is not None:
            logging_setup.log_event(
                "transcribe",
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
                    "transcribe",
                    f"Job #{job.number} abandonado — venció la ventana de "
                    f"gracia sin actividad",
                    level=logging.ERROR,
                )
                self._model_manager.unload()
                self._cleanup(job)
                if self._job is job:
                    self._job = None
