
"""Fábrica de la app FastAPI: monta las rutas y arranca el discovery UDP."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import discovery, logging_setup
from .job_manager import JobManager
from .model_manager import ModelManager
from .translation_model_manager import TranslationModelManager
from .routes import admin, jobs, models, status, translate


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging_setup.log_event("server", "Servidor iniciado")
    stop_discovery = discovery.start()
    yield
    stop_discovery.set()
    logging_setup.log_event("server", "Servidor detenido")


def create_app() -> FastAPI:
    app = FastAPI(title="audiotools-mediator", lifespan=lifespan)

    model_manager = ModelManager()
    translation_model_manager = TranslationModelManager()
    job_manager = JobManager({
        "transcribe": model_manager,
        "translate": translation_model_manager,
    })
    app.state.model_manager = model_manager
    app.state.translation_model_manager = translation_model_manager
    app.state.job_manager = job_manager

    app.include_router(jobs.router)
    app.include_router(models.router)
    app.include_router(translate.router)
    app.include_router(status.router)
    app.include_router(admin.router)

    return app
