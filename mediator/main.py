
"""
Punto de entrada. Arranque manual por ahora (sin systemd ni nada de
fondo):

    python -m mediator.main [--debug] [--host 0.0.0.0] [--port 8000]
"""
from __future__ import annotations

import argparse

import uvicorn

from . import config, logging_setup
from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Server mediador de transcripción")
    parser.add_argument(
        "--debug", action="store_true",
        help="Nivel de log DEBUG (incluye los pings de discovery UDP)",
    )
    parser.add_argument("--host", default=config.HTTP_HOST)
    parser.add_argument("--port", type=int, default=config.HTTP_PORT)
    args = parser.parse_args()

    config.HTTP_PORT = args.port
    logging_setup.setup_logging(debug=args.debug)

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

