"""api.errors unit tests."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meet_transcribe.api.errors import ERROR_CODES, APIError, install_error_handlers


def test_known_codes_have_http_status() -> None:
    for code in ("AUTH_FAIL", "RATE_LIMITED", "INTERNAL", "QUOTA_EXCEEDED"):
        assert code in ERROR_CODES


def test_unknown_code_falls_back_to_internal() -> None:
    err = APIError("BOGUS_CODE", "x")
    assert err.code == "INTERNAL"
    assert err.http_status == 500


def test_payload_does_not_leak_traceback() -> None:
    err = APIError("AUTH_FAIL", "missing token")
    p = err.to_payload()
    assert p == {"code": "AUTH_FAIL", "message": "missing token"}
    assert "traceback" not in p


def test_handler_returns_structured_json() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def _boom() -> None:
        raise APIError("RATE_LIMITED", "slow down")

    with TestClient(app) as client:
        resp = client.get("/boom")
    assert resp.status_code == 429
    assert resp.json() == {"code": "RATE_LIMITED", "message": "slow down"}
