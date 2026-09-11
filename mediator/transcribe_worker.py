"""
Ejecuta la transcripción de un job: carga el modelo, procesa los
archivos en loop (uno por uno), manda progreso/resultados por el WS del
job, y borra cada audio apenas termina de procesarse.

``model.transcribe()`` es una llamada bloqueante (CPU/GPU) — igual que
``WhisperRunner`` en audio_tools la corre en un thread aparte para no
trabar el loop de eventos, acá usamos ``run_in_executor`` y un puente
thread-safe (``call_soon_threadsafe``) hacia la cola asyncio del job.

No hay chequeo previo de VRAM (decisión consciente): si la combinación
pedida no entra en memoria, la carga o la inferencia van a tirar una
excepción, que acá se captura y se reporta por WS + log, liberando el
job en vez de dejarlo colgado.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from pathlib import Path
from typing import Any

from . import constants, logging_setup

logger = logging_setup.get_logger()


def _push(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, message: dict) -> None:
    loop.call_soon_threadsafe(queue.put_nowait, message)


def _run_one_file_sync(
    model,
    file_index: int,
    filename: str,
    audio_path: Path,
    duration: float,
    job_config: dict[str, Any],
    completed_before: int,
    total_files: int,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
    touch,
    is_cancelled,
) -> list[dict]:
    """
    Corre DENTRO del thread del executor (bloqueante). Devuelve los
    segments de este archivo ya serializados (solo start/end/text, que
    es lo único que hoy usan build_srt/build_vtt/build_txt del lado
    cliente).

    Si ``is_cancelled()`` se vuelve True a mitad de camino, corta el
    loop de segmentos ahí mismo — el archivo queda incompleto y quien
    llama (``process_job``) decide qué hacer con eso (no se manda
    ``result`` para un archivo cancelado a mitad de camino).
    """
    language = constants.LANGUAGES.get(job_config["language"])
    initial_prompt = job_config["initial_prompt"] or None

    _push(loop, queue, {"type": "log", "message": f"Transcribiendo {filename}..."})
    touch()

    segments_gen, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=job_config["beam_size"],
        vad_filter=job_config["vad_filter"],
        condition_on_previous_text=job_config["condition_on_previous_text"],
        word_timestamps=job_config["word_timestamps"],
        initial_prompt=initial_prompt,
    )

    _push(loop, queue, {
        "type": "log",
        "message": f"Idioma detectado: '{info.language}' (probabilidad {info.language_probability:.4f})",
    })

    collected: list[dict] = []
    for segment in segments_gen:
        if is_cancelled():
            break
        collected.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
        })
        touch()

        file_progress = min(segment.end / duration, 1.0) if duration > 0 else 0.0
        value = (completed_before + file_progress) / total_files if total_files else 0.0
        _push(loop, queue, {
            "type": "progress",
            "value": value,
            "file_index": file_index,
            "file_progress": file_progress,
            "completed": completed_before,
            "total": total_files,
        })

    if not is_cancelled():
        final_value = (completed_before + 1) / total_files if total_files else 1.0
        _push(loop, queue, {
            "type": "progress",
            "value": final_value,
            "file_index": file_index,
            "file_progress": 1.0,
            "completed": completed_before + 1,
            "total": total_files,
        })
    return collected


async def process_job(job, job_manager, model_manager) -> None:
    loop = asyncio.get_running_loop()
    queue = job.outbound
    total_files = len(job.files)
    cfg = job.config

    def touch() -> None:
        job_manager.touch(job.job_id)

    # ── carga del modelo ────────────────────────────────────────────
    job.state = "loading_model"
    logging_setup.log_event(
        "transcribe", f"Job #{job.number} — cargando modelo {cfg['model_size']}...",
    )
    await queue.put({"type": "log", "message": f"Cargando modelo {cfg['model_size']}..."})

    load_start = time.monotonic()
    try:
        model = await loop.run_in_executor(
            None, model_manager.load, cfg["model_size"], cfg["device"], cfg["compute_type"],
        )
    except Exception as exc:
        elapsed = time.monotonic() - load_start
        logging_setup.log_event(
            "transcribe",
            f"Job #{job.number} — falló la carga del modelo tras {elapsed:.1f}s: {exc}\n"
            f"{traceback.format_exc()}",
            level=logging.ERROR,
        )
        await queue.put({
            "type": "error",
            "message": f"No se pudo cargar el modelo '{cfg['model_size']}': {exc}",
            "file_index": None,
        })
        await queue.put({"type": "done", "success": False, "cancelled": False})
        job.state = "failed"
        await job_manager.finish(job.job_id)
        return

    load_elapsed = time.monotonic() - load_start
    logging_setup.log_event(
        "transcribe", f"Job #{job.number} — modelo cargado ({load_elapsed:.1f}s)",
    )
    job.state = "processing"
    touch()

    # ── procesamiento en loop, archivo por archivo ──────────────────
    success = True
    cancelled = False
    for rf in job.files:
        if job.cancelled:
            cancelled = True
            break

        await queue.put({"type": "file_start", "file_index": rf.index})
        file_start = time.monotonic()
        try:
            segments = await loop.run_in_executor(
                None,
                _run_one_file_sync,
                model, rf.index, rf.filename, rf.path, rf.duration, cfg,
                rf.index, total_files, loop, queue, touch, lambda: job.cancelled,
            )
        except Exception as exc:
            file_elapsed = time.monotonic() - file_start
            logging_setup.log_event(
                "transcribe",
                f"Job #{job.number} — falló '{rf.filename}' tras {file_elapsed:.1f}s: {exc}\n"
                f"{traceback.format_exc()}",
                level=logging.ERROR,
            )
            await queue.put({"type": "error", "message": str(exc), "file_index": rf.index})
            success = False
        else:
            if job.cancelled:
                # Se cortó a mitad de este archivo — no se manda result,
                # queda incompleto (su audio se borra igual, en el finally).
                cancelled = True
                logging_setup.log_event(
                    "transcribe",
                    f"Job #{job.number} — '{rf.filename}' cancelado a mitad de proceso",
                    level=logging.WARNING,
                )
            else:
                file_elapsed = time.monotonic() - file_start
                logging_setup.log_event(
                    "transcribe",
                    f"Job #{job.number} — archivo transcripto: {rf.filename} ({file_elapsed:.1f}s)",
                )
                await queue.put({
                    "type": "result",
                    "file_index": rf.index,
                    "filename": rf.filename,
                    "segments": segments,
                })
        finally:
            rf.path.unlink(missing_ok=True)
            touch()

        if job.cancelled:
            cancelled = True
            break

    await queue.put({"type": "done", "success": success and not cancelled, "cancelled": cancelled})
    job.state = "done" if (success and not cancelled) else "failed"
    await job_manager.finish(job.job_id)
