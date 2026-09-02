"""Configuración y rutas del mediador."""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

WHISPER_MODELS_DIR = BASE_DIR / "whisper_models"
LOGS_DIR = BASE_DIR / "logs"

HTTP_HOST = "0.0.0.0"
HTTP_PORT = 8000

UDP_DISCOVERY_PORT = 50505
UDP_DISCOVERY_MAGIC = b"AUDIOTOOLS_DISCOVER?"
SERVICE_NAME = "audiotools-mediator"
SERVICE_VERSION = 1

# Ventana de gracia para jobs "zombie" (WS desconectado, o archivos que
# nunca terminan de llegar) antes de cancelar y liberar el modelo.
GRACE_PERIOD_SECONDS = 15 * 60  # 15 minutos
WATCHDOG_INTERVAL_SECONDS = 30  # cada cuánto se revisa la inactividad

WHISPER_MODELS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
