
"""
Maneja la sesión WS completa de un job de traducción.

A diferencia de transcribe_worker.py (que recorre una lista de
archivos ya subidos, de forma autónoma), acá el mediador nunca sabe de
antemano cuántas llamadas va a hacer el cliente ni con qué contenido —
lo maneja el cliente, que decide bloque por bloque (y reintento por
reintento) qué mandar a continuación, exactamente como lo haría con el
modelo cargado en su propio proceso (ver core/translation/model_manager.py
en audio_tools). Por eso la cancelación tampoco necesita protocolo acá:
el cliente simplemente deja de mandar "generate" y cierra la sesión —
nunca hay un loop autónomo del lado del mediador que haya que
interrumpir a mitad de camino.

Protocolo, sobre el mismo WS todo el tiempo:

    servidor -> cliente   {"type": "ready", "vram": {...} | null}
                           una vez, apenas termina de cargar el modelo
    servidor -> cliente   {"type": "error", "message": str}
                           carga del modelo fallida, o falla durante
                           una generación — el job igual se libera
    cliente  -> servidor  {"type": "generate", "system_prompt": str,
                            "user_message": str, "temperature": float}
    servidor -> cliente   {"type": "delta", "text": str}   (0 o más)
    servidor -> cliente   {"type": "result"}               (una vez,
                           cierra esa generación puntual)
    cliente  -> servidor  {"type": "close"}
                           fin de la sesión — el mediador libera el
                           modelo de inmediato y cierra el WS

Cada "generate" es bloqueante desde el punto de vista del cliente (no
manda el siguiente hasta recibir "result") — por eso no hace falta
correlacionar requests con un id: nunca hay más de una llamada en
vuelo en la misma sesión.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
import traceback
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from . import hardware_info, logging_setup

logger = logging_setup.get_logger()

# Sentinel interno (nunca se manda por WS) para que el loop de drenado
# de _run_generate sepa cuándo la llamada bloqueante terminó — ver el
# mismo patrón (_push + call_soon_threadsafe) en transcribe_worker.py.
_GENERATE_DONE = {"type": "__generate_done__"}


def _push(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, message: dict) -> None:
    loop.call_soon_threadsafe(queue.put_nowait, message)


async def _run_generate(
    websocket: WebSocket,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
    model_manager,
    request: dict[str, Any],
) -> None:
    system_prompt = request.get("system_prompt", "")
    user_message = request.get("user_message", "")
    temperature = request.get("temperature", 0.3)

    def on_delta(text: str) -> None:
        _push(loop, queue, {"type": "delta", "text": text})

    def blocking_call() -> None:
        try:
            model_manager.generate(system_prompt, user_message, temperature, on_delta)
        except Exception as exc:
            logging_setup.log_event(
                "translate",
                f"Error durante la generación: {exc}\n{traceback.format_exc()}",
                level=logging.ERROR,
            )
            _push(loop, queue, {
                "type": "error",
                "message": f"Error durante la generación:\n{traceback.format_exc()}",
            })
            _push(loop, queue, _GENERATE_DONE)
            return
        _push(loop, queue, {"type": "result"})
        _push(loop, queue, _GENERATE_DONE)

    executor_future = loop.run_in_executor(None, blocking_call)
    while True:
        message = await queue.get()
        if message is _GENERATE_DONE:
            break
        await websocket.send_json(message)
    await executor_future


async def run_session(websocket: WebSocket, job, job_manager, model_manager) -> None:
    """
    Corre durante toda la vida del WS: carga el modelo, avisa "ready",
    y despues procesa "generate"/"close" uno a la vez hasta que el
    cliente cierre la sesion o se desconecte. Libera el modelo (y el
    job) pase lo que pase — no depende del watchdog para el caso
    normal, ese queda solo como red de seguridad ante un crash.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cfg = job.config

    def touch() -> None:
        job_manager.touch(job.job_id)

    try:
        # ── carga del modelo ────────────────────────────────────────
        job.state = "loading_model"
        logging_setup.log_event(
            "translate", f"Job #{job.number} — cargando modelo {cfg['model']}...",
        )
        load_start = time.monotonic()
        try:
            await loop.run_in_executor(
                None, model_manager.load, cfg["model"], cfg["n_gpu_layers"], cfg["n_ctx"],
            )
        except Exception as exc:
            error_msg = str(exc)
            
            # Detectar si es un problema de VRAM
            if "out of memory" in error_msg.lower() or "cudamalloc" in error_msg.lower():
                detailed_msg = (
                    f"Insuficiente VRAM: no hay memoria en GPU para cargar el modelo "
                    f"con n_gpu_layers={cfg['n_gpu_layers']}. "
                    f"Intenta reducir n_gpu_layers (ej: {max(1, cfg['n_gpu_layers'] - 10)}) "
                    f"o usa un modelo más pequeño."
                )
            else:
                detailed_msg = error_msg
            
            # Enviar error al cliente
            await websocket.send_json({
                "type": "error",
                "message": detailed_msg
            })
            logging_setup.log_event(
                job.kind,
                f"Job #{job.number} — falló la carga del modelo: {detailed_msg}",
                level=logging.ERROR,
            )
            return

        load_elapsed = time.monotonic() - load_start
        logging_setup.log_event(
            "translate", f"Job #{job.number} — modelo cargado ({load_elapsed:.1f}s)",
        )
        job.state = "processing"
        touch()

        vram = hardware_info.get_vram_snapshot()
        await websocket.send_json({
            "type": "ready",
            "vram": dataclasses.asdict(vram) if vram else None,
        })

        # ── loop principal: una llamada "generate" a la vez ─────────
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                logging_setup.log_event(
                    "translate",
                    f"Job #{job.number} — el cliente se desconectó sin cerrar la sesión",
                    level=logging.WARNING,
                )
                break

            touch()
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            mtype = data.get("type")
            if mtype == "close":
                logging_setup.log_event(
                    "translate", f"Job #{job.number} — sesión cerrada por el cliente",
                )
                break
            if mtype == "generate":
                await _run_generate(websocket, queue, loop, model_manager, data)

        job.state = "done"

    except Exception as exc:
        logging_setup.log_event(
            "translate",
            f"Job #{job.number} — error inesperado en la sesión: {exc}\n{traceback.format_exc()}",
            level=logging.ERROR,
        )
        job.state = "failed"

    finally:
        await job_manager.finish(job.job_id)
        try:
            await websocket.close()
        except Exception:
            pass
