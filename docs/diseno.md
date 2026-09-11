# Server mediador de transcripción — Diseño consolidado

## 1. Arquitectura general

- Un solo proceso **mediador** (FastAPI), corriendo indefinidamente en el PC remoto dentro de la LAN.
- **Repo propio**, independiente de `audio_tools` — sin dependencia de `core/`, `pywebview`, etc.
- Un solo cliente a la vez (uso personal, sin necesidad de manejar concurrencia entre múltiples clientes).
- El mediador carga/descarga el modelo whisper **en su propia memoria** (lazy load, unload explícito y por timeout) — no lanza servers hijos.
- El repo `audio_tools` (cliente) suma un módulo nuevo que habla con el mediador por red. El resto del proyecto (UI Svelte, eventos `audiotools:*`, `TranscribeAction`, etc.) **no cambia**.

## 2. Descubrimiento (sin IP fija)

- **UDP broadcast**. El mediador escucha en un puerto UDP fijo conocido (además del puerto TCP de FastAPI), en un thread aparte.
- El cliente manda un paquete broadcast tipo `"AUDIOTOOLS_DISCOVER?"` a la subred.
- El mediador responde con su IP y el puerto donde vive FastAPI/WS.
- Timeout corto (~1-2s) del lado cliente antes de reintentar o fallar.

## 3. Modelos disponibles

- Carpeta local en el mediador (ej. `whisper_models/`) con los modelos ct2 copiados a mano por vos (mismo patrón que `ModelManager.list_models()` ya usa hoy para translate).
- `GET /whisper_models` — lista lo que está físicamente presente. **Sin descarga automática**: si se pide un modelo que no está, se rechaza.
- *(Feature futura, no ahora: endpoint para pedir la descarga de un modelo desde Hugging Face, con feedback de progreso de descarga.)*

## 4. Ciclo de vida de un job

1. **`POST /jobs`** — el cliente manda la config completa (ver sección 5). Todos los campos son obligatorios: el mediador no aplica ningún valor por default (si falta alguno, `422`).
   - Mediador libre → reserva el slot, responde `200` con `job_id` + URL del WS.
   - Mediador ocupado (modelo cargado / job en curso) → responde `409` con el motivo explícito, sugiriendo `DELETE /model` si corresponde.
   - **No hay chequeo previo de VRAM disponible** — decisión consciente: si la combinación `model_size`/`device`/`compute_type` pedida no entra en memoria, va a fallar recién al cargar o durante la inferencia (ver sección 6, manejo de crasheos).
2. El **cliente abre el WebSocket** hacia `ws://.../jobs/{job_id}/ws` usando el `job_id` recibido. A partir de acá hay feedback en vivo (y el WS funciona como heartbeat).
3. El cliente sube los archivos **uno por uno**, vía `POST /jobs/{job_id}/files` (mejor granularidad de feedback que un multipart único con todo el lote).
4. El mediador confirma la recepción de cada archivo por WS.
5. Una vez recibidos todos, el mediador **carga el modelo** (lazy) y procesa **en loop, archivo por archivo**:
   - logs/progreso de ese archivo por WS — misma forma que hoy usa el frontend: `audiotools:log {message}`, `audiotools:progress {value, file_index, file_progress, completed, total}`.
   - al terminar ese archivo: manda el resultado (segments — solo `start/end/text`, sin datos por palabra) por WS.
   - borra el audio de ese archivo del disco del mediador.
   - sigue con el próximo.
6. Al terminar todos: `audiotools:done {success}` por WS, y el mediador **libera el modelo de memoria inmediatamente** (no espera el timeout de inactividad — ese timeout es solo para el caso zombie).

## 5. Config que viaja en `POST /jobs`

Mismo contrato que `TranscribeConfig` hoy:

`model_size, device, compute_type, language, vad_filter, condition_on_previous_text, word_timestamps, beam_size, initial_prompt, output_format`

La duración (`total_duration`) se sigue calculando en el **cliente** (con el `ffprobe` ya embebido) y se manda como metadata junto con cada archivo.

## 6. Protección contra procesos zombie y fallas de memoria

- WS cortado a mitad de proceso (subida o procesamiento) → **15 min de gracia** para reconectar al mismo `job_id` antes de cancelar el job y liberar el modelo.
- Solicitud aceptada (paso 1) pero nunca llegan archivos → mismo timeout de **15 min**.
- **Falla al cargar el modelo o durante la inferencia** (ej. CUDA out of memory — no hay chequeo previo de VRAM, es una decisión consciente) → se captura la excepción, se reporta por WS como error explícito con el mensaje real, y se loguea como `ERROR` con traceback completo. El slot se libera de inmediato — el mediador no queda trabado en estado `loading`/`busy` esperando algo que ya falló.
- `DELETE /model` — fuerza la liberación manual en cualquier momento.
- `GET /status` — `{"state": "idle" | "loading" | "busy", "model": str | null, "job_id": str | null}`, sin autenticación. Le permite al cliente chequear antes de intentar un `POST /jobs`, y sirve para debug manual.

## 7. Transporte

- **HTTP** (FastAPI): `POST /jobs`, `POST /jobs/{id}/files`, `GET /whisper_models`, `GET /status`, `DELETE /model`.
- **WebSocket** por job: logs, progreso, resultado por archivo, done.
- **Sin autenticación** — red de confianza, uso personal.

## 8. Qué NO cambia en el cliente (`audio_tools`)

- Frontend Svelte: cero cambios — mismos eventos `audiotools:*`, mismos payloads.
- Duración: se sigue calculando localmente con `ffprobe`.
- Escritura final del `.srt`/`.vtt`/`.txt`: sigue del lado cliente vía `TranscribeAction` ya existente. El mediador no necesita saber nada de `base_folder`.

## 9. Logging (solo del lado mediador, por ahora)

- Módulo `logging` estándar de Python, con `TimedRotatingFileHandler` (rotación a medianoche).
- Carpeta `logs/` dentro del propio repo del mediador.
- Un archivo por día: `transcribe_2026-08-31.log`, `transcribe_2026-09-01.log`, etc. — sin archivo separado por job (se puede sumar más adelante si hace falta, sin romper nada de lo existente).
- Sin borrado/retención automática — crecen indefinidamente, se gestionan a mano.
- Formato de línea: `[Hora][tool] Acción` — hora con milisegundos, sin fecha (ya implícita en el nombre del archivo). El segundo campo es `transcribe`, `translate`, o `server` (eventos generales, no atados a un tool). Cada job se identifica dentro del texto con un contador simple que arranca en 1 y se reinicia con cada archivo de log nuevo (`Job #1`, `Job #2`...), no con el id técnico interno:

  ```
  [14:00:00,000][server]     Servidor iniciado — escuchando en 192.168.1.50:8000
  [14:32:01,123][transcribe] Job #1 aceptado — modelo large-v1, device cuda, compute_type int8_float16, 3 archivos esperados
  [14:32:04,880][transcribe] Job #1 — archivo recibido: ep01.mp3 (42.1 MB)
  [14:32:05,010][transcribe] Job #1 — cargando modelo large-v1...
  [14:33:12,442][transcribe] Job #1 — modelo cargado (67.4s)
  [14:41:50,201][transcribe] Job #1 — archivo transcripto: ep01.mp3 (524.9s)
  [14:55:03,776][transcribe] Job #1 — WebSocket desconectado, ventana de gracia hasta las 15:10:03
  [15:10:03,776][transcribe] Job #1 abandonado — venció la ventana de gracia sin reconexión
  [16:00:00,000][server]     Servidor detenido
  ```

- **Eventos cubiertos** (nivel `INFO`/`WARN`/`ERROR`): arranque/apagado del server; `POST /jobs` aceptado o rechazado (+ motivo); cada archivo recibido (nombre, tamaño); carga y descarga del modelo (inicio, éxito con tiempo que tardó, o falla con motivo — incluyendo fallas de memoria, ver sección 6); inicio/fin de transcripción por archivo con tiempo que tardó; fin del job; desconexión de WS y vencimiento de la ventana de gracia; excepciones no manejadas con traceback completo.
- **Nivel `DEBUG`** (apagado por defecto): pings de discovery (UDP) y cualquier otro ruido de alta frecuencia que no aporte a una auditoría de errores. Se activa con el flag `--debug` al arrancar (`python mediator.py --debug`) — el mediador se arranca manualmente por ahora, sin servicio de fondo.

## 10. Entorno del PC remoto (donde corre el mediador)

- OS: Garuda Linux (Arch), kernel `7.1.11-zen1`.
- CPU: AMD FX-6300 (6 núcleos, 2012) — de sobra para FastAPI y la decodificación de audio; si algún día se usa `device=cpu` para transcribir en este mediador, va a ser notablemente lento comparado con una máquina moderna.
- RAM: ~7.9GB total.
- GPU: GTX 1650 SUPER, 4096MiB de VRAM.
- Con el escritorio KDE Plasma (Wayland) completo corriendo, el compositor + shell usan ~307MiB de VRAM en reposo — quedan **~3.7GB libres** igual, sin necesidad del modo bajo consumo para que entren los modelos grandes en `int8`.
- Driver NVIDIA (`nvidia-smi` 610.57.04) soporta hasta CUDA 13.3 — muy por encima de lo que pide `ctranslate2` (CUDA 12 + cuDNN 9). No hace falta instalar el toolkit de CUDA del sistema: alcanza con `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` dentro del entorno virtual del mediador + `LD_LIBRARY_PATH` apuntando a esos paquetes (comando exacto cuando armemos el setup del repo).

## 11. Cancelación

- El cliente manda `{"type": "cancel"}` por el WS del job.
- El mediador marca un flag `cancelled` en el `Job` (mismo patrón cooperativo que `WhisperRunner.cancel()` en local): se chequea entre segmentos y entre archivos, no interrumpe abruptamente en medio de una llamada a `model.transcribe()`.
- El archivo que estaba a mitad de transcribirse en el momento de la cancelación se descarta (no se manda `result` para ese, se borra su audio igual que cualquier archivo procesado). Los archivos ya completados antes de la cancelación (que ya mandaron su `result`) quedan como están, no se tocan.
- No se arranca ningún archivo siguiente del lote.
- El modelo se libera de inmediato (no se espera el timeout de 15 minutos).
- El `done` final: `{"type": "done", "success": false, "cancelled": true}` — el campo `cancelled` distingue esto de una falla real.

## 12. Forma final de los mensajes WS

Ya implementado, esto reemplaza lo que antes era un pendiente:

- `{"type": "file_received", "file_index": int, "filename": str, "files_received": int, "files_expected": int}` — confirmación de cada subida.
- `{"type": "log", "message": str}`.
- `{"type": "file_start", "file_index": int}` — arranca a procesar ese archivo. Existe específicamente para que el cliente pueda mapearlo 1:1 a `on_file_start(i)`, igual que hace `WhisperRunner` en local.
- `{"type": "progress", "value": float, "file_index": int, "file_progress": float, "completed": int, "total": int}` — `file_progress` es la fracción del archivo actual (0–1); `value` es la fracción del lote completo. `WhisperRunner.on_progress` local solo maneja `file_progress` — el cliente remoto debe usar ese campo, no `value`, para llamar a `on_progress`.
- `{"type": "result", "file_index": int, "filename": str, "segments": [{"start": float, "end": float, "text": str}, ...]}`.
- `{"type": "error", "message": str, "file_index": int | null}`.
- `{"type": "done", "success": bool, "cancelled": bool}`.

Y del cliente hacia el mediador, por el mismo WS: `{"type": "cancel"}`.

## Pendientes de implementación (no bloquean el diseño)

- Lógica de reconexión de WS del lado cliente (reintentos durante la ventana de 15 min) — el módulo cliente actual no la implementa todavía, se conecta una sola vez por job.
- Manejo de errores de subida (archivo corrupto, desconexión a mitad de un `POST /jobs/{id}/files`).
