"""tests for /health and /ready 占位端点。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok() -> None:
    from meet_transcribe.api.app import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_ready_m0_placeholder() -> None:
    from meet_transcribe.api.app import app

    client = TestClient(app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "loading"}
