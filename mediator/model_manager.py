
"""
Gestión del modelo whisper: carga perezosa, reuso mientras no cambie la
config, y descarga explícita — mismo patrón que ``WhisperRunner`` en
audio_tools (core/transcription/whisper_runner.py), pero disparado por
requests HTTP en vez de llamadas locales, y resolviendo el modelo a una
ruta local en vez de un nombre que faster-whisper podría intentar
descargar de Hugging Face.
"""
from __future__ import annotations

import gc
import threading

from faster_whisper import WhisperModel

from . import models_registry


class ModelManager:
    def __init__(self) -> None:
        self._model: WhisperModel | None = None
        self._key: tuple | None = None
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def loaded_model_size(self) -> str | None:
        return self._key[0] if self._key else None

    def load(self, model_size: str, device: str, compute_type: str) -> WhisperModel:
        """
        Carga el modelo si no está cargado ya con la misma key. Puede
        lanzar una excepción (ej. CUDA out of memory, o el modelo no
        está presente) — eso se maneja arriba, en el worker del job,
        no acá.
        """
        key = (model_size, device, compute_type)
        with self._lock:
            if self._model is None or self._key != key:
                if self._model is not None:
                    self._unload_locked()
                model_path = models_registry.resolve_model_path(model_size)
                self._model = WhisperModel(
                    str(model_path), device=device, compute_type=compute_type,
                )
                self._key = key
            return self._model

    def unload(self) -> None:
        with self._lock:
            self._unload_locked()

    def _unload_locked(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            self._key = None
            gc.collect()

