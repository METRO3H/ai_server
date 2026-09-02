"""
Esquemas de request/response de la API HTTP.

Ningún campo de config tiene default: el cliente define todo, siempre
— si falta algo, FastAPI devuelve 422 automáticamente.
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from . import constants


class JobRequest(BaseModel):
    model_size: str
    device: str
    compute_type: str
    language: str
    vad_filter: bool
    condition_on_previous_text: bool
    word_timestamps: bool
    beam_size: int
    initial_prompt: str
    files_expected: int

    @field_validator("device")
    @classmethod
    def _check_device(cls, v: str) -> str:
        if v not in constants.DEVICES:
            raise ValueError(f"device debe ser uno de {constants.DEVICES}")
        return v

    @field_validator("language")
    @classmethod
    def _check_language(cls, v: str) -> str:
        if v not in constants.LANGUAGES:
            raise ValueError(f"language debe ser uno de {list(constants.LANGUAGES)}")
        return v

    @field_validator("beam_size")
    @classmethod
    def _check_beam_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError("beam_size debe ser >= 1")
        return v

    @field_validator("files_expected")
    @classmethod
    def _check_files_expected(cls, v: int) -> int:
        if v < 1:
            raise ValueError("files_expected debe ser >= 1")
        return v

    @model_validator(mode="after")
    def _check_compute_type(self) -> "JobRequest":
        valid = constants.COMPUTE_TYPES_BY_DEVICE.get(self.device, [])
        if self.compute_type not in valid:
            raise ValueError(
                f"compute_type '{self.compute_type}' no es válido para device "
                f"'{self.device}' (válidos: {valid})"
            )
        return self


class JobCreatedResponse(BaseModel):
    job_id: str
    job_number: int
    ws_path: str


class StatusResponse(BaseModel):
    state: str  # "idle" | "loading" | "busy"
    model: str | None
    job_id: str | None


class ModelsResponse(BaseModel):
    models: list[str]
