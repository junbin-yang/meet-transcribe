"""结构化日志（structlog + JSON）。

启动时由 api/app.py 调用 init_logging(cfg)；之后所有模块用
    log = structlog.get_logger(__name__)

输出格式：单行 JSON，含 ts / level / event / logger / 任意 kv。
敏感字段（api_key/ticket/password/embedding 等）统一脱敏。
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

_REDACTED = "***"
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "ticket",
        "password",
        "passwd",
        "secret",
        "token",
        "kms_key",
        "embedding",
    }
)


def _redact_processor(
    _logger: Any, _name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for k in list(event_dict.keys()):
        if k.lower() in _SENSITIVE_KEYS:
            event_dict[k] = _REDACTED
    return event_dict


def _add_logger_name(
    logger: Any, _name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    name = getattr(logger, "name", None)
    if name:
        event_dict.setdefault("logger", name)
    return event_dict


def _add_log_level(
    _logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    if method_name == "warn":
        method_name = "warning"
    event_dict.setdefault("level", method_name)
    return event_dict


def init_logging(level: str = "INFO", fmt: str = "json") -> None:
    """初始化 structlog；只应在进程启动时调用一次。"""

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_log_level,
        _add_logger_name,
        timestamper,
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: Any = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=shared + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
