from __future__ import annotations

import asyncio
import json

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from .. import models_registry
from ..job_manager import BusyError
from ..schemas import JobCreatedResponse, JobRequest
from ..transcribe_worker import process_job

router = APIRouter()


@router.post("/jobs", response_model=JobCreatedResponse)
async def create_job(payload: JobRequest, request: Request):
    job_manager = request.app.state.job_manager

    available = models_registry.list_available_models()
    if payload.model_size not in available:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El modelo '{payload.model_size}' no está disponible en este "
                f"mediador. Modelos presentes: {available or 'ninguno'}."
            ),
        )

    try:
        job = await job_manager.create_job(payload.model_dump(), payload.files_expected)
    except BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return JobCreatedResponse(
        job_id=job.job_id, job_number=job.number, ws_path=f"/jobs/{job.job_id}/ws",
    )


@router.post("/jobs/{job_id}/files")
async def upload_file(
    job_id: str,
    request: Request,
    file: UploadFile = File(...),
    duration: float = Form(...),
):
    job_manager = request.app.state.job_manager
    try:
        job = job_manager.get_for(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Job no encontrado (¿venció o ya terminó?)")

    content = await file.read()
    try:
        rf = await job_manager.receive_file(job_id, file.filename or "archivo", duration, content)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if job_manager.all_files_received(job_id):
        asyncio.create_task(process_job(job, job_manager, request.app.state.model_manager))

    return {
        "file_index": rf.index,
        "filename": rf.filename,
        "received": len(job.files),
        "expected": job.files_expected,
    }


@router.websocket("/jobs/{job_id}/ws")
async def job_ws(websocket: WebSocket, job_id: str):
    job_manager = websocket.app.state.job_manager
    try:
        job = job_manager.attach_ws(job_id, websocket)
    except KeyError:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    async def sender() -> None:
        while True:
            message = await job.outbound.get()
            await websocket.send_json(message)
            if message.get("type") == "done":
                break

    async def receiver() -> None:
        try:
            while True:
                text = await websocket.receive_text()
                job_manager.touch(job_id)
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(data, dict) and data.get("type") == "cancel":
                    job_manager.request_cancel(job_id)
        except WebSocketDisconnect:
            pass

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    _, pending = await asyncio.wait(
        {sender_task, receiver_task}, return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()

    job_manager.detach_ws(job_id)
