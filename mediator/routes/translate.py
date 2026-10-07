
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, WebSocket

from .. import translate_worker
from ..job_manager import BusyError
from ..schemas import JobCreatedResponse, ModelsResponse, TranslateJobRequest

router = APIRouter()


@router.get("/translation_models", response_model=ModelsResponse)
async def get_translation_models(request: Request):
    manager = request.app.state.translation_model_manager
    return ModelsResponse(models=manager.list_models())


@router.post("/translate/jobs", response_model=JobCreatedResponse)
async def create_translate_job(payload: TranslateJobRequest, request: Request):
    job_manager = request.app.state.job_manager
    manager = request.app.state.translation_model_manager

    available = manager.list_models()
    if payload.model not in available:
        raise HTTPException(
            status_code=409,
            detail=(
                f"El modelo '{payload.model}' no está disponible en este "
                f"mediador. Modelos presentes: {available or 'ninguno'}."
            ),
        )

    try:
        job = await job_manager.create_job(
            "translate", payload.model_dump(), files_expected=0,
        )
    except BusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return JobCreatedResponse(
        job_id=job.job_id, job_number=job.number,
        ws_path=f"/translate/jobs/{job.job_id}/ws",
    )


@router.websocket("/translate/jobs/{job_id}/ws")
async def translate_job_ws(websocket: WebSocket, job_id: str):
    job_manager = websocket.app.state.job_manager
    manager = websocket.app.state.translation_model_manager
    try:
        job = job_manager.get_for(job_id)
    except KeyError:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    # No pasa por job_manager.attach_ws()/detach_ws() a propósito: esos
    # existen para el flujo de reconexión con ventana de gracia de
    # transcribe (donde el WS y la subida de archivos son cosas
    # separadas). Acá el WS ES la sesión completa — si se corta, la
    # sesión termina (ver translate_worker.run_session, que libera el
    # modelo en su `finally` pase lo que pase, sin esperar al
    # watchdog). El watchdog general sigue activo como red de
    # seguridad ante un crash del proceso, no como mecanismo normal.
    await translate_worker.run_session(websocket, job, job_manager, manager)
