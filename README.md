# AI SERVER

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/downloads/)
[![Platform Linux](https://img.shields.io/badge/platform-linux-lightgrey)](https://www.kernel.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![faster-whisper](https://img.shields.io/badge/faster--whisper-1.0%2B-blue)](https://github.com/SYSTRAN/faster-whisper)
[![CUDA 12](https://img.shields.io/badge/CUDA-12-76B900?logo=nvidia)](https://developer.nvidia.com/cuda-toolkit)
[![ctranslate2](https://img.shields.io/badge/ctranslate2-4.7.2-blue)](https://github.com/OpenNMT/CTranslate2)

A transcription mediator server for `audio_tools` — runs on a dedicated LAN machine, loads a Whisper model on demand, and transcribes audio files sent over the network.

Standalone repo with no dependency on the `audio_tools` project itself (no `core/`, no `pywebview`). The client side talks to it over HTTP + WebSocket and reuses the same event contract the local `WhisperRunner` already emits, so the Svelte frontend doesn't need to change.

See `docs/diseno.md` for the full design rationale (in Spanish) behind every protocol decision.

---

## Features

- **Single-process FastAPI server** running indefinitely on the LAN — no child processes, no worker pool.
- **Lazy model loading** — the Whisper model is loaded into the mediator's own memory on the first job that needs it, and unloaded immediately when the job finishes.
- **UDP broadcast discovery** — the client finds the mediator without a static IP.
- **Per-job WebSocket** for live logs, progress, per-file results, and cancellation.
- **Same event shape** as the local `WhisperRunner` (`audiotools:log`, `audiotools:progress`, etc.) so the client-side mapping is 1:1.
- **Cooperative cancellation** — the client can send `{"type": "cancel"}` mid-job; the mediator stops between segments and between files.
- **Zombie protection** — 15-minute grace window for reconnects, plus explicit `DELETE /model` to force-release a stuck job.
- **Daily rotating logs** at `logs/transcribe_YYYY-MM-DD.log`, no automatic retention.
- **Rich progress bars in the server terminal** for model loading and per-file transcription.

---

## Requirements

- Python 3.10+
- Linux (tested on Garuda Linux / Arch, kernel `7.1.11-zen1`)
- NVIDIA GPU with a driver supporting CUDA 12 (a GTX 1650 SUPER with 4 GB VRAM is enough — see [Model sizing](#model-sizing))

No system-wide CUDA toolkit is required. The pip packages `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` (already in `requirements.txt`) ship everything `ctranslate2` / `faster-whisper` need.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Making the NVIDIA libs visible to Python

The pip-installed CUDA libraries live under `site-packages/nvidia/.../lib`, and `ctranslate2` needs them on `LD_LIBRARY_PATH` at runtime. You can either:

**Option A — the launcher script does it for you.** `run.fish` computes the path from the venv's Python at startup, so you never have to think about it:

```fish
./run.fish
```

**Option B — set it permanently in fish** (so plain `python -m mediator.main` also works):

```fish
# with the venv activated:
set -Ux LD_LIBRARY_PATH (python3 -c "import nvidia.cublas, nvidia.cudnn, os; print(os.path.join(nvidia.cublas.__path__[0], 'lib') + ':' + os.path.join(nvidia.cudnn.__path__[0], 'lib'))")
```

Note: `nvidia.cublas.lib` is **not** an importable module — it's an implicit-namespace package folder with no `__init__.py`, so it has no `__file__`. You have to walk it from `nvidia.cublas.__path__[0]` and append `/lib` yourself. The one-liner above does that.

Sanity check:

```fish
echo $LD_LIBRARY_PATH
ls (echo $LD_LIBRARY_PATH | string split ':')[1]
```

You should see `libcublas.so.12` (or similar). If that fails, something didn't resolve correctly — fix it before starting the server.

---

## Models

Whisper models are **not downloaded automatically**. Drop the CTranslate2 model folders you want into `whisper_models/`, one folder per model:

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

`GET /whisper_models` returns whatever it finds there. Requesting a model that isn't present is rejected with `409` — the mediator never reaches out to Hugging Face.

### Where to get the files

Since the mediator may have limited bandwidth, download on a well-connected machine and copy over LAN (`scp`, a network share, a USB stick — whatever's convenient):

```bash
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-large-v3', local_dir='large-v3')"
```

Replace `large-v3` with whichever size you want — the HF repo names follow `Systran/faster-whisper-<size>` (e.g. `Systran/faster-whisper-medium`).

This produces a flat `large-v3/` folder (`model.bin`, `config.json`, etc., without the symlink-tree layout of the normal HF cache). Copy that folder as-is into `whisper_models/` on the mediator.

---

## Running the server

```bash
python -m mediator.main --debug
```

| Flag | Default | Description |
|------|---------|-------------|
| `--debug` | off | Enables DEBUG log level (includes UDP discovery pings). |
| `--host` | `0.0.0.0` | Bind address for the HTTP/WS server. |
| `--port` | `8000` | HTTP/WS port. |

Or, using the launcher script (handles `LD_LIBRARY_PATH`, optional power-saving mode, and clean shutdown on Ctrl+C):

```fish
./run.fish
```

`run.fish` accepts `--no-power-mode` to skip the power-saving toggle, and forwards any remaining args to the server (e.g. `./run.fish --debug`).

Logs go to `logs/transcribe_YYYY-MM-DD.log`, one file per day, no automatic cleanup.

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/whisper_models` | List available models. |
| `GET` | `/status` | `{"state": "idle" \| "loading" \| "busy", "model": str \| null, "job_id": str \| null}`. Unauthenticated. |
| `POST` | `/jobs` | Create a job. Accepts (`200` + `job_id`) or rejects (`409` with an explicit reason). All config fields are required — no defaults. |
| `POST` | `/jobs/{job_id}/files` | Upload one file (`multipart/form-data`: `file` + `duration` in seconds). |
| `WS` | `/jobs/{job_id}/ws` | Live logs, progress, per-file results, and `done`. Also accepts `{"type": "cancel"}` from the client. |
| `DELETE` | `/model` | Force-release the loaded model and clear the current job, regardless of state. |

### `POST /jobs` body

```json
{
  "model_size": "large-v3",
  "device": "cuda",
  "compute_type": "int8_float16",
  "language": "auto",
  "vad_filter": true,
  "condition_on_previous_text": false,
  "word_timestamps": true,
  "beam_size": 5,
  "initial_prompt": "",
  "files_expected": 3
}
```

Valid values (mirrored from `core/transcription/whisper_runner.py` in `audio_tools`):

- `device`: `cuda` | `cpu`
- `compute_type` for `cuda`: `float16` | `int8_float16` | `int8`
- `compute_type` for `cpu`: `float32` | `int8`
- `language`: `auto` | `ja` | `zh` | `ko` | `es` | `en`
- `beam_size`: integer ≥ 1
- `files_expected`: integer ≥ 1

### WebSocket messages

**Server → client:**

```jsonc
{"type": "file_received", "file_index": 0, "filename": "ep01.mp3", "files_received": 1, "files_expected": 3}
{"type": "log", "message": "Loading model large-v3..."}
{"type": "file_start", "file_index": 0}
{"type": "progress", "value": 0.42, "file_index": 0, "file_progress": 0.85, "completed": 0, "total": 3}
{"type": "result", "file_index": 0, "filename": "ep01.mp3", "segments": [{"start": 0.0, "end": 2.5, "text": "..."}]}
{"type": "error", "message": "...", "file_index": 0}
{"type": "done", "success": true, "cancelled": false}
```

Notes:

- `file_progress` is the fraction of the current file (0–1). `value` is the fraction of the whole batch. The local `WhisperRunner.on_progress` only uses `file_progress` — the remote client should do the same.
- `segments` contain only `start` / `end` / `text`, which is all `build_srt` / `build_vtt` / `build_txt` need on the client side.
- The final `done` carries `"cancelled": true` when the job was stopped by the client, to distinguish it from a real failure.

**Client → server:**

```json
{"type": "cancel"}
```

Cancellation is cooperative: it stops between segments and between files, does not abort a call to `model.transcribe()` in progress. The file that was mid-transcription when cancellation arrived is discarded (no `result` sent for it, its audio is deleted). Files that already completed before the cancel keep their results. The next file in the batch is never started.

---

## Design notes

### Discovery

The mediator listens on a fixed UDP port (default `50505`) in a background thread, in addition to the FastAPI TCP port. The client broadcasts `AUDIOTOOLS_DISCOVER?` to the subnet; the mediator replies with a JSON payload containing the service name, version, and HTTP port. The client already knows the IP from the reply's source address.

### Job lifecycle

1. Client sends `POST /jobs` with full config. Mediator reserves the slot and returns `job_id` + WS path. If busy, returns `409` with an explicit reason.
2. Client opens the WebSocket. This is also the heartbeat.
3. Client uploads files one at a time via `POST /jobs/{job_id}/files` (finer-grained feedback than a single multipart batch).
4. Mediator acknowledges each file over the WS (`file_received`).
5. Once all files arrive, the mediator loads the model (lazy) and processes files sequentially. Per-file results are sent as they complete; each file's audio is deleted from disk immediately after processing.
6. On completion, `done` is sent and the model is unloaded immediately — the mediator doesn't wait for the inactivity timeout (that's only for zombie jobs).

### Zombie protection

- WS dropped mid-job → 15-minute grace window to reconnect to the same `job_id`.
- Job accepted but no files arrive → same 15-minute timeout.
- Model load or inference failure (e.g. CUDA OOM) → the exception is caught, reported over WS with the full traceback, logged at `ERROR`, and the slot is freed immediately — the mediator never stays stuck in `loading` / `busy`.
- `DELETE /model` force-releases in any state.

### Logging

Python's standard `logging` module, daily rotation by filename (`transcribe_YYYY-MM-DD.log`). Line format:

```
[HH:MM:SS,mmm][tool] message
```

`tool` is one of `server`, `transcribe`, `translate`. Jobs are identified with a human-readable counter (`Job #1`, `Job #2`, ...) that resets daily, not the internal `job_id`.

Example:

```
[14:00:00,000][server]     Servidor iniciado — escuchando en 192.168.1.50:8000
[14:32:01,123][transcribe] Job #1 aceptado — modelo large-v1, device cuda, compute_type int8_float16, 3 archivos esperados
[14:32:04,880][transcribe] Job #1 — archivo recibido: ep01.mp3 (42.1 MB)
[14:32:05,010][transcribe] Job #1 — cargando modelo large-v1...
[14:33:12,442][transcribe] Job #1 — modelo cargado (67.4s)
[14:41:50,201][transcribe] Job #1 — archivo transcripto: ep01.mp3 (524.9s)
[15:10:03,776][transcribe] Job #1 abandonado — venció la ventana de gracia sin reconexión
[16:00:00,000][server]     Servidor detenido
```

`--debug` enables DEBUG level (UDP discovery pings and other high-frequency noise), off by default.

---

## Model sizing

Reference machine: AMD FX-6300, ~7.9 GB RAM, GTX 1650 SUPER with 4096 MiB VRAM.

With KDE Plasma (Wayland) fully running, the compositor + shell use roughly 307 MiB of VRAM at idle, leaving about **3.7 GB free**. `large-v3` at `int8` fits comfortably.

The driver (`nvidia-smi` 610.57.04) supports up to CUDA 13.3 — well above what `ctranslate2` requires (CUDA 12 + cuDNN 9).

### Pinned `ctranslate2`

`ctranslate2` is pinned to `4.7.2` on purpose. `4.8.x` introduces a noticeably higher VRAM peak with `beam_size=10` on `large-v3` — empirically confirmed as an OOM on the 4 GB GTX 1650, while `4.7.2` handles the same workload without issues, even with multiple queued files. Do not bump this without re-testing that edge case.

---

## Power mode helper scripts

`run.fish` optionally enables a power-saving mode before starting the server, and restores it on exit. Two helper scripts handle the toggle:

- **`power_mode_on.fish`** — stops `app-*.service` user units (except Konsole), kills known heavy processes (`dolphin`, `firefox`, `chromium`, `code`, `kwrite`, `localsend`), masks `kde-baloo`, `plasma-baloorunner`, `plasma-krunner`, `xdg-desktop-portal-gtk`, and stops `plasma-plasmashell`. Prints RAM and VRAM freed. Pass `-v` for verbose output.
- **`power_mode_off.fish`** — reverses the above: unmasks services, restarts `plasma-plasmashell`, and starts the autostart services (`kdeconnect`, `discover.notifier`, `geoclue-demo-agent`, `xwaylandvideobridge`).

Skip power mode entirely with `./run.fish --no-power-mode`.

`get_pc_info.fish` is a diagnostic script that dumps session type, GPU, VRAM consumers, top RAM processes, running user/system systemd services, CPU governor, and power manager status — useful when tuning the machine for this workload.

---

## Testing

A manual smoke test covers the full job lifecycle without a real GPU or model:

```bash
python tests/manual_smoke_test.py
```

It mocks `WhisperModel` and exercises:

1. A successful end-to-end job (POST /jobs → WS → upload → progress → result → done → model released → status back to `idle`).
2. A model-load failure (simulating a CUDA OOM) — reported over WS, mediator recovers and accepts new jobs afterwards.
3. Real cancellation mid-file — `cancel` sent during the first of two files, verifying no `result` is sent for the aborted file and the next file is never started.

Not a replacement for testing with a real model and GPU on the target machine.

---

## Known limitations

- **No VRAM check before accepting a job** — conscious decision. If the requested `model_size` / `device` / `compute_type` combination doesn't fit, it fails at load time or during inference and is reported as an explicit error.
- **Per-file failures don't abort the batch** — if one file fails during processing, a WS `error` message is sent for that file and the job continues with the rest. (This behavior wasn't explicitly specified; it's the interpretation that seemed most reasonable and can be changed if the whole job should cancel on first failure.)
- **Client-side WS reconnection is not implemented yet** — the current client module connects once per job. The mediator-side 15-minute grace window is in place for when reconnection lands.
- **Upload error handling is minimal** — corrupt files and mid-upload disconnections over `POST /jobs/{id}/files` are not specially handled yet.
- **No authentication** — designed for a trusted LAN, personal use.
- **Discovery always listening, but the process itself is not a service** — the UDP listener runs whenever the Python process runs; running that process as a background service (systemd, etc.) is not set up yet.

---

## Author

[METRO3H](https://github.com/METRO3H)