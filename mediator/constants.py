"""
Tablas de valores válidos para la config de transcripción.

Reflejan las mismas tablas de core/transcription/whisper_runner.py en
audio_tools, para que el mediador pueda rechazar con un motivo claro
(422) si el cliente manda algo inválido, en vez de fallar de forma
oscura más adelante.
"""
from __future__ import annotations

DEVICES = ["cuda", "cpu"]

COMPUTE_TYPES_BY_DEVICE: dict[str, list[str]] = {
    "cuda": ["float16", "int8_float16", "int8"],
    "cpu": ["float32", "int8"],
}

# "auto" -> None (autodetección de idioma), igual que LANGUAGES en
# whisper_runner.py.
LANGUAGES: dict[str, str | None] = {
    "auto": None,
    "ja": "ja",
    "zh": "zh",
    "ko": "ko",
    "es": "es",
    "en": "en",
}
