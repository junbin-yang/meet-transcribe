"""tests for /health, /ready, /metrics 端点。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok() -> None:
    from meet_transcribe.api.app import app

    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_ready_reports_db_check() -> None:
    """/ready 返回结构化 checks；DB 未连通时 status=fail。"""
    from meet_transcribe.api.app import app

    with TestClient(app) as client:
        resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "checks" in body
    assert "db" in body["checks"]


def test_metrics_endpoint_returns_prometheus_text() -> None:
    from meet_transcribe.api.app import app

    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert b"meet_transcribe_" in resp.content
