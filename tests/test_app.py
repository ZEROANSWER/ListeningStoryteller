from fastapi.testclient import TestClient

import app as app_module
from app import app


client = TestClient(app)


def test_home_page_is_available():
    response = client.get("/")
    assert response.status_code == 200
    assert "识语绘声" in response.text


def test_health_does_not_expose_secret():
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ready", "degraded"}
    assert isinstance(payload["ffmpeg"], bool)
    assert isinstance(payload["api_key_configured"], bool)
    assert "DASHSCOPE_API_KEY" not in response.text


def test_unknown_task_returns_404():
    response = client.get("/api/process/not-a-task")
    assert response.status_code == 404


def test_audio_endpoint_rejects_non_mp3():
    response = client.get("/api/audio/story.json")
    assert response.status_code == 404


def test_upload_reports_missing_ffmpeg(monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda _: None)
    response = client.post(
        "/api/upload",
        files={"file": ("recording.webm", b"not-used", "audio/webm")},
    )
    assert response.status_code == 503
    assert "ffmpeg" in response.json()["detail"]
