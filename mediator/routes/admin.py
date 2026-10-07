
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.delete("/model")
async def release_model(request: Request):
    job_manager = request.app.state.job_manager
    message = await job_manager.force_release()
    return {"message": message}

