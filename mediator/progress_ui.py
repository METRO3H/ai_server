"""
Barra de progreso para la terminal del server, durante la carga del
modelo y la transcripción de cada job.

Usa el mismo ``Console`` de rich que ``logging_setup`` (ver
``logging_setup.console``) para que las líneas de log de siempre
(job aceptado, archivo recibido, etc.) se sigan viendo intercaladas
arriba de la barra en vez de corromper el redibujado en vivo.

Hay dos barras simultáneas mientras se procesa un job:

- "general": progreso del batch completo (archivos ya completados +
  fracción del archivo actual, sobre el total de archivos del job).
- "archivo actual": progreso del archivo que se está transcribiendo
  en este momento (0-100%).

Mientras se carga el modelo (antes de tener cualquier archivo en
proceso) se muestra en su lugar un spinner con barra indeterminada.
"""
from __future__ import annotations

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from . import logging_setup


class ProgressUI:
    """Un ciclo de vida completo de barra(s) para UN job a la vez —
    se crea una instancia nueva por job en ``transcribe_worker.py``."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=logging_setup.console,
            transient=False,
        )
        self._loading_task: TaskID | None = None
        self._overall_task: TaskID | None = None
        self._file_task: TaskID | None = None
        self._running = False

    # ── ciclo de vida ────────────────────────────────────────────────

    def start(self) -> None:
        if not self._running:
            self._progress.start()
            self._running = True

    def stop(self) -> None:
        if self._running:
            self._progress.stop()
            self._running = False

    # ── carga del modelo (spinner indeterminado) ────────────────────

    def set_loading(self, model_size: str) -> None:
        self._loading_task = self._progress.add_task(
            f"Cargando modelo {model_size}...", total=None,
        )

    def model_loaded(self) -> None:
        if self._loading_task is not None:
            self._progress.remove_task(self._loading_task)
            self._loading_task = None

    # ── procesamiento (barra general + barra del archivo actual) ────

    def start_job(self, job_number: int, total_files: int) -> None:
        self._overall_task = self._progress.add_task(
            f"Job #{job_number} — progreso general", total=total_files,
        )

    def set_file(self, file_index: int, total_files: int, filename: str) -> None:
        description = f"Archivo {file_index + 1}/{total_files} — {filename}"
        if self._file_task is None:
            self._file_task = self._progress.add_task(description, total=1.0)
        else:
            self._progress.reset(
                self._file_task, total=1.0, completed=0.0, description=description,
            )

    def update(self, *, file_progress: float, completed_before: int) -> None:
        if self._file_task is not None:
            self._progress.update(self._file_task, completed=file_progress)
        if self._overall_task is not None:
            self._progress.update(
                self._overall_task, completed=completed_before + file_progress,
            )