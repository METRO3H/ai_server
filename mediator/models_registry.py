
"""
Modelos de whisper disponibles en este mediador.

Se copian a mano en ``whisper_models/<model_size>/`` (formato ct2, el
que espera faster-whisper) — sin descarga automática desde Hugging
Face. Si se pide un modelo que no está en esta carpeta, se rechaza.
"""
from __future__ import annotations

from pathlib import Path

from . import config


def list_available_models() -> list[str]:
    """Escanea whisper_models/ y devuelve los nombres de carpeta que
    parecen un modelo ct2 válido (tienen model.bin adentro)."""
    if not config.WHISPER_MODELS_DIR.exists():
        return []
    found = []
    for entry in sorted(config.WHISPER_MODELS_DIR.iterdir()):
        if entry.is_dir() and (entry / "model.bin").exists():
            found.append(entry.name)
    return found


def resolve_model_path(model_size: str) -> Path:
    """Devuelve la ruta local del modelo, o lanza FileNotFoundError si
    no está — nunca dispara una descarga."""
    path = config.WHISPER_MODELS_DIR / model_size
    if not path.is_dir() or not (path / "model.bin").exists():
        raise FileNotFoundError(
            f"El modelo '{model_size}' no está disponible en este mediador "
            f"(se esperaba encontrarlo en {path})."
        )
    return path

