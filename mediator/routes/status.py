from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import StatusResponse

router = APIRouter()


@router.get("/status", response_model=StatusResponse)
async def get_status(request: Request):
    job_manager = request.app.state.job_manager
    return StatusResponse(**job_manager.status())
