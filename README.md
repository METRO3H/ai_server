# audiotools-mediator

Server mediador de transcripción para `audio_tools` — corre en un PC
dedicado dentro de la LAN, carga el modelo whisper bajo demanda y
transcribe lo que le llega por red. Repo independiente, sin ninguna
dependencia del proyecto `audio_tools` (ni de `core/`, ni de `pywebview`).

Ver `docs/diseno.md` para el detalle completo del protocolo y el
razonamiento detrás de cada decisión.

## Setup (Linux, GPU NVIDIA)

Requiere Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

No hace falta instalar el toolkit de CUDA del sistema — `nvidia-cublas-cu12`
y `nvidia-cudnn-cu12` (ya en `requirements.txt`) traen las librerías que
necesita `ctranslate2`/`faster-whisper`. Solo falta que el intérprete las
encuentre en tiempo de ejecución. Como acá el shell es `fish`, conviene
setearlo como variable **universal** (una sola vez, queda para siempre en
todas las sesiones futuras, sin tocar ningún archivo de config a mano) en
vez de un `export` que hay que repetir cada vez que abrís una terminal
nueva:

```fish
# con el venv activado, una sola vez:
set -Ux LD_LIBRARY_PATH (python3 -c "import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ':' + os.path.dirname(nvidia.cudnn.lib.__file__))")
```

Con eso ya no hace falta pensarlo de nuevo — queda seteado para siempre en
cualquier terminal `fish` de esa cuenta, incluida la próxima vez que
prendas la máquina.

## Modelos

Copiá a mano las carpetas de los modelos ct2 de whisper que quieras
tener disponibles, dentro de `whisper_models/`, una carpeta por modelo:

```
whisper_models/
├── medium/
│   ├── model.bin
│   ├── config.json
│   └── ...
└── large-v3/
    ├── model.bin
    └── ...
```

`GET /whisper_models` lista lo que encuentra ahí. Si se pide un modelo que no
está, el mediador rechaza el job — nunca descarga nada automáticamente.

**¿De dónde salen esos archivos?** Como el mediador tiene poco ancho de
banda, no conviene descargarlos ahí directamente (`large-v3` son ~3GB).
Mejor bajarlos en una máquina con buena conexión (ej. tu PC de Windows) y
pasarlos por LAN. No hace falta instalar `faster-whisper` entero para
esto, alcanza con:

```bash
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-large-v3', local_dir='large-v3')"
```

(cambiá `large-v3` por el tamaño que quieras — los nombres de repo son
`Systran/faster-whisper-<tamaño>`, ej. `Systran/faster-whisper-medium`).
Eso deja una carpeta `large-v3/` ya en formato plano (`model.bin`,
`config.json`, etc., sin la estructura rara de symlinks que usa la caché
normal de Hugging Face) — copiás esa carpeta tal cual dentro de
`whisper_models/` en el mediador (`scp`, un recurso compartido de red, un
pendrive, lo que te resulte más cómodo).

## Arrancar el server

```bash
python -m mediator.main --debug
```

`--debug` prende el nivel de log DEBUG (incluye los pings de discovery
UDP). Sin el flag, el log queda en INFO. El server se arranca a mano
por ahora, sin servicio de fondo (`systemd` u otro) — queda como algo
a definir más adelante.

Los logs quedan en `logs/transcribe_YYYY-MM-DD.log`, un archivo por
día, sin borrado automático.

## API

- `GET /whisper_models` — modelos disponibles.
- `GET /status` — `{"state": "idle"|"loading"|"busy", "model": ..., "job_id": ...}`, sin autenticación.
- `POST /jobs` — acepta (`200` + `job_id`) o rechaza (`409` con el motivo) un job nuevo. Todos los campos de config son obligatorios, sin defaults.
- `POST /jobs/{job_id}/files` — sube un archivo (uno por vez, `multipart/form-data`: `file` + `duration`).
- `WS /jobs/{job_id}/ws` — logs, progreso y resultados en vivo, mismo contrato que los eventos `audiotools:*` del cliente local. El cliente puede mandar `{"type": "cancel"}` por este mismo WS para cortar el job (corta entre segmentos/archivos, no espera a terminar el lote completo; el archivo a mitad de proceso se descarta sin mandar `result`, los ya completados quedan como están). El `done` final incluye `"cancelled": true/false` para distinguir un corte pedido por el usuario de una falla real.
- `DELETE /model` — libera el modelo a la fuerza, sin importar el estado del job.

## Notas de implementación / simplificaciones conocidas

- No hay chequeo de VRAM antes de aceptar un job (decisión consciente) — si la
  combinación pedida no entra en memoria, se reporta como error al cargar o
  durante la inferencia, no de forma preventiva.
- Si un archivo individual falla durante el procesamiento, el job sigue con
  el resto (se manda un mensaje `error` para ese archivo por WS, no se
  aborta todo el lote). No se discutió explícitamente este caso — es la
  interpretación que me pareció más razonable, ajustable si preferís que
  todo el job se cancele ante la primera falla.
- El descubrimiento UDP siempre está escuchando mientras el proceso corre
  (no hace falta systemd para eso), pero el proceso en sí solo existe
  mientras lo tengas arrancado a mano.
