"""
Gestión del modelo de traducción (llama-cpp-python / GGUF): carga
perezosa, reuso mientras no cambie la config, descarga explícita, y UNA
llamada de generación con streaming crudo (delta por delta, sin
acumular ni throttlear acá).

Mismo patrón que ``model_manager.py`` (whisper) para el ciclo de vida
del modelo, pero la parte de inferencia es deliberadamente mínima: este
mediador NO sabe nada de bloques de subtítulos, reintentos, detección
de idioma de salida, ni fallback offline — toda esa lógica sigue
viviendo, sin duplicar, en ``core/translation/model_manager.py`` de
audio_tools (ver ``ModelManager.translate_block`` ahí). Acá solo se
expone el equivalente remoto de un ÚNICO ``Llama.create_chat_completion``
— el cliente arma cuántas llamadas necesite (intento normal + cualquier
reintento) y decide qué hacer con cada resultado, exactamente igual que
si el modelo estuviera cargado en su propio proceso.

Requiere ``llama-cpp-python`` — ver README.md para cómo instalarlo con
soporte CUDA en este mediador (no es un simple ``pip install``, a
diferencia de faster-whisper).
"""
from __future__ import annotations

import gc
import io
import sys
import threading
from contextlib import redirect_stderr
from typing import Callable

from . import config


class TranslationModelManager:
    def __init__(self) -> None:
        self._model = None
        self._key: tuple | None = None
        self._lock = threading.RLock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def loaded_model_size(self) -> str | None:
        """Nombre del .gguf cargado — mismo nombre de propiedad que usa
        ``model_manager.py`` (whisper) para que ``JobManager.status()``
        pueda leer cualquiera de los dos managers sin distinguir tipos."""
        return self._key[0] if self._key else None

    def list_models(self) -> list[str]:
        if not config.TRANSLATION_MODELS_DIR.exists():
            return []
        return sorted(p.name for p in config.TRANSLATION_MODELS_DIR.glob("*.gguf"))

    def load(self, model: str, n_gpu_layers: int, n_ctx: int):
        """Carga el modelo si no está cargado ya con la misma key.
        Captura stderr de llama.cpp para mostrar errores informativos
        (VRAM, arquitectura, etc.).
        Puede lanzar una excepción (modelo no encontrado, CUDA out of
        memory, etc.) — eso se maneja arriba, en el worker del job."""
        from llama_cpp import Llama  # import perezoso: no hace falta

        key = (model, n_gpu_layers, n_ctx)
        with self._lock:
            if self._model is not None and self._key == key:
                return self._model
            if self._model is not None:
                self._unload_locked()

            model_path = config.TRANSLATION_MODELS_DIR / model
            if not model_path.exists():
                raise FileNotFoundError(
                    f"El modelo '{model}' no está disponible en este mediador "
                    f"(se esperaba encontrarlo en {model_path})."
                )

            # Capturar stderr de llama.cpp para errores informativos
            stderr_capture = io.StringIO()
            try:
                with redirect_stderr(stderr_capture):
                    self._model = Llama(
                        model_path=str(model_path),
                        n_gpu_layers=n_gpu_layers,
                        n_ctx=n_ctx,
                        # chat_format="chatml",
                        verbose=False,
                    )
            except Exception as exc:
                stderr_output = stderr_capture.getvalue()
                # Extraer las líneas más informativas del stderr
                detailed_errors = self._extract_meaningful_errors(stderr_output)
                
                # Armar mensaje de error con contexto
                error_lines = [str(exc)]
                if detailed_errors:
                    error_lines.append("Detalles de llama.cpp:")
                    error_lines.extend(detailed_errors)
                
                raise RuntimeError("\n".join(error_lines))
            finally:
                stderr_capture.close()

            self._key = key
            return self._model

    def _extract_meaningful_errors(self, stderr_output: str) -> list[str]:
        """
        Extrae las líneas más informativas de stderr de llama.cpp.
        Prioriza errores de VRAM, arquitectura, archivo, etc.
        """
        if not stderr_output:
            return []

        lines = stderr_output.split("\n")
        meaningful = []

        # Palabras clave que indican líneas informativas
        keywords = [
            "cudamalloc",
            "out of memory",
            "allocating",
            "unable to allocate",
            "failed to load",
            "error",
            "cuda",
            "vram",
            "buffer",
            "compute capability",
            "suboptimal performance",
        ]

        for line in lines:
            line_lower = line.lower().strip()
            if any(kw in line_lower for kw in keywords) and line_lower:
                # Evitar duplicados y líneas muy largas (metadata)
                if len(line) < 200 and line not in meaningful:
                    meaningful.append(line.strip())

        return meaningful[:5]  # Top 5 líneas más relevantes

    def unload(self) -> None:
        with self._lock:
            self._unload_locked()

    def _unload_locked(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            self._key = None
            gc.collect()

    # ── Generación: UNA llamada al modelo, streaming crudo ────────────

    def generate(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float,
        on_delta: Callable[[str], None],
    ) -> None:
        """
        Hace UNA llamada de chat completion y llama a ``on_delta(text)``
        por cada fragmento de texto que llega del stream — sin
        acumular, sin throttlear, sin post-procesar (ni ``/no_think``
        ni "stripear" bloques ``<think>``: eso ya viene resuelto en
        ``system_prompt``/``user_message``, que el cliente arma
        exactamente igual que para una llamada local — ver
        ``ModelManager._call_model``/``generate_text`` en audio_tools).

        Bloqueante — pensada para correr dentro de un
        ``run_in_executor`` (ver translate_worker.py), igual que
        ``model.transcribe()`` en transcribe_worker.py.
        """
        with self._lock:
            if self._model is None:
                raise RuntimeError("Modelo de traducción no cargado.")

            stream = self._model.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=temperature,
                max_tokens=-1,
                stream=True,
            )
            for chunk in stream:
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    on_delta(delta)