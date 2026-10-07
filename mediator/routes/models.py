
from __future__ import annotations

from fastapi import APIRouter

from .. import models_registry
from ..schemas import ModelsResponse

router = APIRouter()


@router.get("/whisper_models", response_model=ModelsResponse)
async def get_models():
    return ModelsResponse(models=models_registry.list_available_models())

