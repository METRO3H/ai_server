"""
Descubrimiento por UDP broadcast: el cliente manda un paquete con el
"magic" ``AUDIOTOOLS_DISCOVER?`` a la subred, este server responde con
el puerto donde vive la API HTTP/WS. El cliente ya sabe la IP porque es
la misma de donde vino la respuesta — no hace falta mandarla en el
payload.
"""
from __future__ import annotations

import json
import logging
import socket
import threading

from . import config, logging_setup


def _serve_forever(stop_event: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", config.UDP_DISCOVERY_PORT))
    sock.settimeout(1.0)  # para poder revisar stop_event periódicamente

    logging_setup.log_event(
        "server", f"Discovery UDP escuchando en el puerto {config.UDP_DISCOVERY_PORT}",
    )

    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break

        logging_setup.log_event(
            "server", f"Discovery: ping recibido de {addr[0]}", level=logging.DEBUG,
        )

        if data == config.UDP_DISCOVERY_MAGIC:
            response = json.dumps({
                "service": config.SERVICE_NAME,
                "version": config.SERVICE_VERSION,
                "port": config.HTTP_PORT,
            }).encode("utf-8")
            sock.sendto(response, addr)

    sock.close()


def start() -> threading.Event:
    stop_event = threading.Event()
    thread = threading.Thread(target=_serve_forever, args=(stop_event,), daemon=True)
    thread.start()
    return stop_event
