"""SQLAlchemy AsyncEngine / AsyncSession 管理。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from meet_transcribe.config.loader import AppConfig

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine_from_config(cfg: AppConfig) -> AsyncEngine:
    """初始化（或返回已存在）AsyncEngine 单例。"""
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    pwd = cfg.secrets.db_password.get_secret_value()
    url = cfg.database.url.replace("CHANGE_ME", pwd) if pwd else cfg.database.url

    _engine = create_async_engine(
        url,
        pool_size=cfg.database.pool_size,
        pool_pre_ping=True,
        echo=cfg.database.echo,
    )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("DB engine not initialized; call init_engine_from_config first")
    return _engine


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每次请求一个 session。"""
    if _session_factory is None:
        raise RuntimeError("DB engine not initialized")
    async with _session_factory() as session:
        yield session


async def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def _set_session_factory_for_tests(factory: Any) -> None:
    """测试夹具用：手动注入一个 session_factory。"""
    global _session_factory
    _session_factory = factory
