"""pytest 共享夹具。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """构造一个最小可用 yaml，并通过 MT_CONFIG_FILE 暴露给 loader。

    切换 cwd 到 tmp_path，避免 pydantic-settings 自动读到仓库 .env。
    """
    cfg = tmp_path / "meet-transcribe.yaml"
    cfg.write_text(
        """
server:
  port: 8080
database:
  url: "sqlite:///:memory:"
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    prev = os.environ.get("MT_CONFIG_FILE")
    os.environ["MT_CONFIG_FILE"] = str(cfg)

    from meet_transcribe.config.loader import load_config

    load_config.cache_clear()

    try:
        yield cfg
    finally:
        if prev is None:
            os.environ.pop("MT_CONFIG_FILE", None)
        else:
            os.environ["MT_CONFIG_FILE"] = prev
        load_config.cache_clear()
