
"""
Logging del mediador.

- Formato de línea: ``[HH:MM:SS,mmm][tool] mensaje``.
- Un archivo por día en ``logs/ai_server_log_YYYY-MM-DD.log``, sin
  límite de retención (se acumulan, se gestionan a mano).
- Nivel ``DEBUG`` apagado por defecto — se prende con ``--debug`` al
  arrancar el server (ver ``main.py``). Ahí van cosas ruidosas como los
  pings de discovery UDP.
- ``tool`` es ``"server"``, ``"transcribe"`` o (a futuro) ``"translate"``.
- El identificador de job en los mensajes es un contador legible por
  día ("Job #1", "Job #2"...), no el id técnico interno — ese contador
  se reinicia con cada archivo de log nuevo.
- La consola usa un ``rich.console.Console`` compartido (``console``,
  exportado desde este módulo) en vez de un ``StreamHandler`` plano —
  así ``progress_ui.py`` puede dibujar sus barras en vivo sobre la
  misma consola y las líneas de log se siguen viendo intercaladas
  arriba de la barra en vez de corromper el redibujado.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from rich.console import Console

from . import config

_LOGGER_NAME = "mediator"

# Consola compartida: la usan tanto este módulo (para las líneas de
# log de siempre) como progress_ui.py (para las barras de progreso).
console = Console()


class _DailyFileHandler(logging.Handler):
    """
    Handler minimalista que escribe a ``logs/{prefix}_{YYYY-MM-DD}.log``
    y cambia de archivo solo cuando cambia el día — sin timer de fondo,
    sin borrado/retención.
    """

    def __init__(self, log_dir: Path, prefix: str = "ai_server_log"):
        super().__init__()
        self._log_dir = log_dir
        self._prefix = prefix
        self._current_date: str | None = None
        self._stream = None

    def _path_for(self, date_str: str) -> Path:
        return self._log_dir / f"{self._prefix}_{date_str}.log"

    def _ensure_stream(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._current_date:
            if self._stream is not None:
                self._stream.close()
            self._current_date = today
            self._stream = open(self._path_for(today), "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._ensure_stream()
            msg = self.format(record)
            self._stream.write(msg + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        super().close()


class _RichConsoleHandler(logging.Handler):
    """
    Imprime cada línea de log a través del ``Console`` compartido de
    rich (en vez de escribir directo a ``sys.stderr`` como hace un
    ``StreamHandler`` normal). Esto es necesario para que las líneas
    de log no corrompan el redibujado en vivo de las barras de
    progreso de ``progress_ui.py`` cuando están activas — Live/Progress
    de rich sabe "correrse" para arriba solo si el texto se imprime a
    través de ese mismo Console.

    ``highlight=False, markup=False``: el formato de línea usa
    corchetes (``[HH:MM:SS,mmm][tool]``), que en sintaxis de rich son
    marcado de estilo — sin desactivarlo, rich intentaría interpretar
    esos corchetes como tags en vez de imprimirlos tal cual.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            console.print(msg, highlight=False, markup=False)
        except Exception:
            self.handleError(record)


class _DefaultToolFilter(logging.Filter):
    """Si algo loguea sin pasar extra={'tool': ...}, cae en 'server'."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "tool"):
            record.tool = "server"
        return True


_job_counter_lock = threading.Lock()
_job_counter = 0
_job_counter_date: str | None = None


def next_job_number() -> int:
    """
    Contador de jobs legible para los logs ("Job #N"), arranca en 1 y
    se reinicia con cada día nuevo — no es el job_id técnico interno.
    """
    global _job_counter, _job_counter_date
    today = datetime.now().strftime("%Y-%m-%d")
    with _job_counter_lock:
        if today != _job_counter_date:
            _job_counter_date = today
            _job_counter = 0
        _job_counter += 1
        return _job_counter


def setup_logging(debug: bool = False) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="[%(asctime)s,%(msecs)03d][%(tool)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = _DailyFileHandler(config.LOGS_DIR)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_DefaultToolFilter())
    logger.addHandler(file_handler)

    # También a consola — se arranca a mano en una terminal, conviene
    # ver algo mientras corre. Vía el Console de rich (ver arriba) para
    # convivir con las barras de progreso de progress_ui.py.
    console_handler = _RichConsoleHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_DefaultToolFilter())
    logger.addHandler(console_handler)

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_event(tool: str, message: str, level: int = logging.INFO) -> None:
    get_logger().log(level, message, extra={"tool": tool})

