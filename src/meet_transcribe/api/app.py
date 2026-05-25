"""FastAPI 入口。

启动顺序：
  1. 加载配置
  2. 初始化 structlog
  3. 初始化 DB engine
  4. 注册路由 + 错误处理 + 指标 + 静态文件

ASR 引擎按 lazy 模式：第一次 WebSocket 连接才下载/加载模型。
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from meet_transcribe import __version__
from meet_transcribe.api.errors import install_error_handlers
from meet_transcribe.api.routes import admin as admin_routes
from meet_transcribe.api.routes import auth as auth_routes
from meet_transcribe.api.routes import ws as ws_routes
from meet_transcribe.config.loader import AppConfig, load_config
from meet_transcribe.db.session import dispose, get_engine, init_engine_from_config
from meet_transcribe.observability.logging import get_logger, init_logging
from meet_transcribe.observability.metrics import render_latest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    cfg: AppConfig = load_config()
    init_logging(level=cfg.observability.log_level, fmt=cfg.observability.log_format)
    log = get_logger("meet_transcribe.api")
    log.info("starting", version=__version__, model=cfg.asr.model, device=cfg.asr.device)

    init_engine_from_config(cfg)
    log.info("db.engine_ready")

    try:
        yield
    finally:
        await dispose()
        log.info("stopped")


app = FastAPI(
    title="meet-transcribe",
    version=__version__,
    description="Meeting real-time speech-to-text backend (B2B private deployment)",
    lifespan=lifespan,
)

install_error_handlers(app)
app.include_router(auth_routes.router, prefix="/v1/auth")
app.include_router(admin_routes.router, prefix="/v1/admin")
app.include_router(ws_routes.router, prefix="/v1")

_demo_dir = _repo_root() / "web-demo"
if _demo_dir.exists():
    app.mount("/demo", StaticFiles(directory=str(_demo_dir), html=True), name="demo")


@app.get("/health")
def health() -> dict[str, str]:
    """liveness: 进程是否存活。"""
    return {"status": "ok", "version": __version__}


@app.get("/ready")
async def ready() -> dict[str, object]:
    """readiness: 关键依赖是否就绪。"""
    checks: dict[str, str] = {}
    overall = "ok"
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"fail: {type(exc).__name__}"
        overall = "fail"
    return {"status": overall, "checks": checks}


@app.get("/metrics")
def metrics() -> Response:
    body, ctype = render_latest()
    return Response(content=body, media_type=ctype)


def run_cli() -> int:
    """`meet-transcribe` 入口。"""
    import uvicorn

    cfg = load_config()
    uvicorn.run(
        "meet_transcribe.api.app:app",
        host=cfg.server.host,
        port=cfg.server.port,
        workers=cfg.server.workers,
        log_level=cfg.observability.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
