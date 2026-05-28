"""tests for config loader."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_loads_minimum_yaml(tmp_yaml: Path) -> None:
    from meet_transcribe.config.loader import load_config

    cfg = load_config()
    assert cfg.server.port == 8080
    assert cfg.server.workers == 1
    assert cfg.database.url.startswith("sqlite:")
    assert cfg.asr.model == "medium"
    assert cfg.asr.language == "zh"
    assert cfg.observability.log_format == "json"
    assert cfg.security.transcript_encryption_enabled is True


def test_secret_defaults_empty_when_env_missing(tmp_yaml: Path) -> None:
    from meet_transcribe.config.loader import load_config

    for k in ("MT_DB_PASSWORD", "MT_SERVER_SECRET", "MT_KMS_KEY"):
        os.environ.pop(k, None)
    load_config.cache_clear()

    cfg = load_config()
    assert cfg.secrets.db_password.get_secret_value() == ""
    assert cfg.secrets.kms_key.get_secret_value() == ""


def test_workers_must_be_one(tmp_yaml: Path) -> None:
    from pydantic import ValidationError

    from meet_transcribe.config.loader import AppConfig, ServerConfig

    with pytest.raises(ValidationError):
        ServerConfig(workers=2)
    with pytest.raises(ValidationError):
        AppConfig(database={"url": "x"}, server={"workers": 4})


def test_missing_config_raises(tmp_path: Path) -> None:
    from meet_transcribe.config.loader import load_config

    bogus = tmp_path / "does-not-exist.yaml"
    os.environ["MT_CONFIG_FILE"] = str(bogus)
    load_config.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="no config found"):
            load_config()
    finally:
        os.environ.pop("MT_CONFIG_FILE", None)
        load_config.cache_clear()
