
"""
Snapshot de VRAM de la GPU NVIDIA activa EN ESTE MEDIADOR.

Copia deliberada de core/hardware_info.py en audio_tools (mismo formato
de datos, mismo comportamiento best-effort) — no un import compartido,
porque cada proceso mide su propia máquina y este repo no depende de
audio_tools (ver docs/diseno.md). Si algún día hace falta soportar otro
fabricante o cambiar el método de medición, hay que tocar los dos
archivos por separado.

Se usa al terminar de cargar un modelo de traducción (ver
translation_model_manager.py) para mandarle este dato al cliente en el
mensaje "ready" del WS — así, si el cliente traduce en remoto, sus
estadísticas guardan la VRAM/GPU del MEDIADOR (no la de su propia PC,
que ni siquiera hace falta que tenga GPU). El campo `gpu_name` es lo
que distingue una corrida local de una remota al comparar stats
después, sin necesidad de una columna nueva en la base de estadísticas
del cliente.

Requiere el paquete "nvidia-ml-py" (pip install nvidia-ml-py) — el
import sigue siendo `import pynvml`. Si no está instalado, no hay GPU
NVIDIA, o falla por cualquier motivo, devuelve None en vez de lanzar
una excepción — nunca debe romper la carga de un modelo por esto.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VramSnapshot:
    vendor: str          # "nvidia"
    name: str             # ej. "NVIDIA GeForce GTX 1650"
    used_mb: float
    total_mb: float
    method: str            # "nvml"


def get_vram_snapshot(device_index: int = 0) -> VramSnapshot | None:
    """Foto puntual del uso de VRAM del dispositivo NVIDIA `device_index`
    — pensada para llamarse una vez, justo después de cargar el modelo."""
    try:
        import pynvml
    except ImportError:
        return None

    try:
        pynvml.nvmlInit()
    except Exception:
        return None

    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)

        name = pynvml.nvmlDeviceGetName(handle)
        if isinstance(name, bytes):
            name = name.decode("utf-8", errors="ignore")

        mem = pynvml.nvmlDeviceGetMemoryInfo(handle)

        return VramSnapshot(
            vendor="nvidia",
            name=name,
            used_mb=mem.used / (1024 * 1024),
            total_mb=mem.total / (1024 * 1024),
            method="nvml",
        )
    except Exception:
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass
