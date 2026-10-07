
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
set -Ux LD_LIBRARY_PATH (python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.join(nvidia.cublas.__path__[0], 'lib') + ':' + os.path.join(nvidia.cudnn.__path__[0], 'lib'))")
```

(Ojo: `nvidia.cublas.lib` en sí **no** es un módulo de Python — es una
carpeta sin `__init__.py` (paquete de namespace implícito), así que no
tiene `__file__`. Hay que navegar desde `nvidia.cublas.__path__[0]`, que
sí apunta a la carpeta real del paquete, y agregarle `/lib` a mano.)

Verificá que la ruta exista de verdad antes de seguir:

```fish
echo $LD_LIBRARY_PATH
ls (echo $LD_LIBRARY_PATH | string split ':')[1]
```

Esto último debería listar `libcublas.so.12` (o similar) — si tira error
de "No such file or directory", algo no cerró y conviene revisar antes de
arrancar el server.

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

## Traducción remota (llama-cpp-python)

Además de transcribir, este mediador puede traducir por el cliente:
carga un modelo GGUF bajo demanda y expone, por WS, el equivalente
remoto de una única llamada al modelo (`Llama.create_chat_completion`)
— arma bloques, reintentos, detección de idioma de salida y el
traductor de respaldo offline es responsabilidad exclusiva del cliente
(`core/translation/model_manager.py` en audio_tools), este mediador no
sabe nada de eso.

**Instalación**: a diferencia de `faster-whisper`, `llama-cpp-python`
no viene comentado con una versión fija en `requirements.txt` — instalá
según lo que tenga esta máquina:

```bash
# Con GPU NVIDIA (requiere el toolkit de CUDA del sistema, no solo las
# librerías vía pip que ya usa faster-whisper — nvcc tiene que estar
# disponible):
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python

# Sin GPU en este mediador, o para probar el flujo mientras resolvés lo
# anterior (funciona, pero corre en CPU con n_gpu_layers=0):
pip install llama-cpp-python
```

Antes de compilar con CUDA, verificá:

```bash
python3 --version                 # llama-cpp-python soporta 3.10–3.12
grep -c avx2 /proc/cpuinfo         # 0 = esta CPU no tiene AVX2
```

Un CPU sin AVX2 (ej. un FX-6300) puede hacer fallar con "illegal
instruction" a un wheel precompilado en otra máquina — compilar acá
mismo (lo que hace el comando de arriba) evita ese problema, porque
`llama.cpp` detecta las instrucciones disponibles en tiempo de
compilación.

### Modelos de traducción

Copiá a mano los `.gguf` que quieras tener disponibles, sueltos dentro
de `translation_models/` (sin subcarpetas, a diferencia de
`whisper_models/`):

```
translation_models/
├── Qwen3-8B-Q5_K_M.gguf
└── Sugoi-14B-Ultra-Q2_K.gguf
```

`GET /translation_models` lista lo que encuentra ahí. Igual que con
whisper, si se pide un modelo que no está, el mediador rechaza la
sesión — nunca descarga nada.

### Protocolo

- `GET /translation_models` — modelos disponibles.
- `POST /translate/jobs` — `{"model", "n_gpu_layers", "n_ctx"}`, todos
  obligatorios. Acepta (`200` + `job_id`/`ws_path`) o rechaza (`409` si
  el modelo no está o ya hay un job en curso, `422` si falta algún
  campo).
- `WS /translate/jobs/{job_id}/ws` — a diferencia de `/jobs/{id}/ws`
  (transcribe), acá el cliente no sube archivos: manda, una a la vez
  por este mismo WS, tantas llamadas `{"type": "generate",
  "system_prompt", "user_message", "temperature"}` como necesite (una
  por bloque de subtítulos, título, nombre de archivo, o reintento —
  eso lo decide el cliente). El mediador contesta con el streaming
  crudo de esa llamada (`{"type": "delta", "text"}` por cada fragmento
  que genera el modelo) y `{"type": "result"}` al terminar. Antes de la
  primera llamada llega `{"type": "ready", "vram": {...} | null}` una
  vez que el modelo terminó de cargar (o `{"type": "error", "message"}`
  si la carga falló). El cliente cierra la sesión mandando
  `{"type": "close"}` — no hay `{"type": "cancel"}` para esto: como
  cada llamada la dispara el cliente, cancelar es simplemente dejar de
  mandar la siguiente y cerrar.

## Arrancar el server

```bash
python -m mediator.main --debug
```

`--debug` prende el nivel de log DEBUG (incluye los pings de discovery
UDP). Sin el flag, el log queda en INFO. El server se arranca a mano
por ahora, sin servicio de fondo (`systemd` u otro) — queda como algo
a definir más adelante.

Los logs quedan en `logs/ai_server_log_YYYY-MM-DD.log`, un archivo por
día, sin borrado automático.

## API

- `GET /whisper_models` — modelos de transcripción disponibles.
- `GET /translation_models` — modelos de traducción (`.gguf`) disponibles.
- `GET /status` — `{"state": "idle"|"loading"|"busy", "model": ..., "job_id": ...}`, sin autenticación. `model` refleja el job activo, sea del kind que sea.
- `POST /jobs` — transcripción. Acepta (`200` + `job_id`) o rechaza (`409` con el motivo) un job nuevo. Todos los campos de config son obligatorios, sin defaults.
- `POST /jobs/{job_id}/files` — sube un archivo de audio (uno por vez, `multipart/form-data`: `file` + `duration`).
- `WS /jobs/{job_id}/ws` — logs, progreso y resultados en vivo, mismo contrato que los eventos `audiotools:*` del cliente local. El cliente puede mandar `{"type": "cancel"}` por este mismo WS para cortar el job (corta entre segmentos/archivos, no espera a terminar el lote completo; el archivo a mitad de proceso se descarta sin mandar `result`, los ya completados quedan como están). El `done` final incluye `"cancelled": true/false` para distinguir un corte pedido por el usuario de una falla real.
- `POST /translate/jobs` — traducción, ver protocolo arriba.
- `WS /translate/jobs/{job_id}/ws` — ver protocolo arriba.
- `DELETE /model` — libera el modelo a la fuerza (el que corresponda al job activo, sea del kind que sea), sin importar el estado del job.

Solo puede haber UN job en curso a la vez en todo el mediador, sin
importar el kind — transcribir y traducir compiten por la misma VRAM,
así que nunca corren en simultáneo.

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

- `ctranslate2` está fijado a `4.7.2` en `requirements.txt` a propósito —
  `4.8.2` reproduce un OOM confirmado con `large-v3` + `beam_size=10` en
  tarjetas de 4GB (funciona sin problema en 4.7.2, incluso con varios
  archivos en cola). Si en algún momento se necesita actualizar
  `ctranslate2`, volver a probar ese caso límite antes de mergear.

