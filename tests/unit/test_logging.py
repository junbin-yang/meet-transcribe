"""observability.logging unit tests."""

from __future__ import annotations

import json

import structlog


def test_redacts_sensitive_keys(capsys) -> None:
    from meet_transcribe.observability.logging import init_logging

    init_logging(level="INFO", fmt="json")
    log = structlog.get_logger("t")
    log.info("auth_attempt", api_key="mt_supersecret", tenant="acme")

    out = capsys.readouterr().out.strip().splitlines()
    assert out, "expected JSON line on stdout"
    payload = json.loads(out[-1])
    assert payload["api_key"] == "***"
    assert payload["tenant"] == "acme"


def test_outputs_iso_timestamp(capsys) -> None:
    from meet_transcribe.observability.logging import init_logging

    init_logging(level="INFO", fmt="json")
    log = structlog.get_logger("t")
    log.info("hi")
    out = capsys.readouterr().out.strip().splitlines()
    payload = json.loads(out[-1])
    assert "timestamp" in payload
    assert payload["timestamp"].endswith("Z") or "+00:00" in payload["timestamp"]


def test_text_format_is_human_readable(capsys) -> None:
    from meet_transcribe.observability.logging import init_logging

    init_logging(level="INFO", fmt="text")
    log = structlog.get_logger("t")
    log.info("startup", port=8080)
    out = capsys.readouterr().out
    assert "startup" in out
    assert "port" in out
