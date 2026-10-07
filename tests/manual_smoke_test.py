
"""
Smoke test manual del ciclo de vida completo de un job — mockea
WhisperModel para poder correrlo sin GPU ni modelos reales. No usa
pytest a propósito (para no sumar esa dependencia): se corre directo.

    python tests/manual_smoke_test.py

Cubre:
  1. Job exitoso de punta a punta (POST /jobs -> WS -> subida ->
     progreso/resultado/done -> modelo liberado, status vuelve a idle).
  2. Falla al cargar el modelo (ej. lo que pasaría con un CUDA out of
     memory real) -> se reporta por WS, el mediador no queda trabado y
     vuelve a aceptar jobs normalmente después.

No reemplaza probar con un modelo y GPU reales — eso solo se puede
hacer en la máquina destino.
"""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from mediator import config, logging_setup
from mediator import model_manager as mm_module
from mediator.app import create_app

BASE_PAYLOAD = {
    "device": "cuda",
    "compute_type": "int8",
    "language": "ja",
    "vad_filter": True,
    "condition_on_previous_text": False,
    "word_timestamps": True,
    "beam_size": 5,
    "initial_prompt": "",
    "files_expected": 1,
}


class _FakeInfo:
    language = "ja"
    language_probability = 0.987


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


class _FakeModel:
    def transcribe(self, path, **kwargs):
        segments = [_FakeSegment(0.0, 1.2, "hola"), _FakeSegment(1.2, 2.5, "mundo")]
        return iter(segments), _FakeInfo()


def _make_fake_model_dir(name: str) -> None:
    d = config.WHISPER_MODELS_DIR / name
    d.mkdir(exist_ok=True)
    (d / "model.bin").write_bytes(b"fake")


def test_job_exitoso() -> None:
    mm_module.WhisperModel = lambda path, device, compute_type: _FakeModel()
    _make_fake_model_dir("fake_ok")

    app = create_app()
    payload = {**BASE_PAYLOAD, "model_size": "fake_ok"}

    with TestClient(app) as client:
        job = client.post("/jobs", json=payload).json()

        with client.websocket_connect(job["ws_path"]) as ws:
            r = client.post(
                f"/jobs/{job['job_id']}/files",
                files={"file": ("audio1.mp3", io.BytesIO(b"fake-audio"), "audio/mpeg")},
                data={"duration": "2.5"},
            )
            assert r.status_code == 200, r.text

            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg["type"] == "done":
                    break

        types_seen = {m["type"] for m in messages}
        assert {"file_received", "progress", "result", "done"} <= types_seen

        result_msg = next(m for m in messages if m["type"] == "result")
        assert result_msg["segments"] == [
            {"start": 0.0, "end": 1.2, "text": "hola"},
            {"start": 1.2, "end": 2.5, "text": "mundo"},
        ]
        assert messages[-1] == {"type": "done", "success": True, "cancelled": False}
        assert client.get("/status").json() == {"state": "idle", "model": None, "job_id": None}

    print("OK — job exitoso de punta a punta")


def test_falla_al_cargar_modelo() -> None:
    def _raise(path, device, compute_type):
        raise RuntimeError("CUDA out of memory (simulado)")

    mm_module.WhisperModel = _raise
    _make_fake_model_dir("fake_fail")

    app = create_app()
    payload = {**BASE_PAYLOAD, "model_size": "fake_fail"}

    with TestClient(app) as client:
        job = client.post("/jobs", json=payload).json()

        with client.websocket_connect(job["ws_path"]) as ws:
            client.post(
                f"/jobs/{job['job_id']}/files",
                files={"file": ("a.mp3", io.BytesIO(b"x"), "audio/mpeg")},
                data={"duration": "1.0"},
            )
            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg["type"] == "done":
                    break

        assert any(m["type"] == "error" for m in messages)
        assert messages[-1] == {"type": "done", "success": False, "cancelled": False}
        assert client.get("/status").json() == {"state": "idle", "model": None, "job_id": None}

        # El mediador tiene que seguir aceptando jobs con normalidad
        r2 = client.post("/jobs", json=payload)
        assert r2.status_code == 200, r2.text

    print("OK — una falla de carga no deja el mediador trabado")


class _SlowFakeModel:
    """Como _FakeModel, pero con una pausa entre segmentos — para poder
    mandar un cancel a mitad de camino en el test."""

    def transcribe(self, path, **kwargs):
        import time

        def gen():
            for i in range(10):
                time.sleep(0.15)
                yield _FakeSegment(i, i + 1, f"seg{i}")

        return gen(), _FakeInfo()


def test_cancelacion_real() -> None:
    mm_module.WhisperModel = lambda path, device, compute_type: _SlowFakeModel()
    _make_fake_model_dir("fake_cancel")

    app = create_app()
    payload = {**BASE_PAYLOAD, "model_size": "fake_cancel", "files_expected": 2}

    with TestClient(app) as client:
        job = client.post("/jobs", json=payload).json()

        with client.websocket_connect(job["ws_path"]) as ws:
            client.post(
                f"/jobs/{job['job_id']}/files",
                files={"file": ("a.mp3", io.BytesIO(b"x"), "audio/mpeg")},
                data={"duration": "10"},
            )
            client.post(
                f"/jobs/{job['job_id']}/files",
                files={"file": ("b.mp3", io.BytesIO(b"x"), "audio/mpeg")},
                data={"duration": "10"},
            )

            # dejamos que arranque a procesar el primer archivo, y recién
            # ahí cancelamos — para probar que corta a mitad de camino
            progress_seen = 0
            while progress_seen < 3:
                msg = ws.receive_json()
                if msg["type"] == "progress":
                    progress_seen += 1
            ws.send_json({"type": "cancel"})

            messages = []
            while True:
                msg = ws.receive_json()
                messages.append(msg)
                if msg["type"] == "done":
                    break

        types_seen = [m["type"] for m in messages]
        assert "result" not in types_seen, "no debería haber result del archivo cancelado a mitad"
        assert messages[-1] == {"type": "done", "success": False, "cancelled": True}
        assert client.get("/status").json() == {"state": "idle", "model": None, "job_id": None}

    print("OK — cancelación real corta a mitad de archivo y no arranca el siguiente")


if __name__ == "__main__":
    logging_setup.setup_logging(debug=False)
    test_job_exitoso()
    test_falla_al_cargar_modelo()
    test_cancelacion_real()
    print("\nTodos los smoke tests pasaron.")

