"""FastAPI 入口。

启动顺序：
  1. 加载配置
  2. 初始化 structlog
  3. 初始化 DB engine
  4. 注册路由 + 错误处理 + 指标 + 静态文件

ASR 引擎按 lazy 模式：第一次 WebSocket 连接才下载/加载模型。
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 显式把 .env 注入 os.environ，让 huggingface_hub / faster-whisper 等第三方库
# 也能读到 HF_ENDPOINT / HF_HUB_OFFLINE 等。pydantic-settings 只映射到自家字段。
try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from meet_transcribe import __version__
from meet_transcribe.api.errors import install_error_handlers
from meet_transcribe.api.routes import admin as admin_routes
from meet_transcribe.api.routes import auth as auth_routes
from meet_transcribe.api.routes import speakers as speakers_routes
from meet_transcribe.api.routes import transcribe as transcribe_routes
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

    try:
        init_engine_from_config(cfg)
        log.info("db.engine_ready")
    except Exception as exc:
        log.error("db.engine_init_failed", error=type(exc).__name__, detail=str(exc))

    # 预热 CAM++ speaker 模型
    if cfg.diarization.enabled:
        try:
            from meet_transcribe.speakers.embedding import _lazy_load

            await asyncio.to_thread(_lazy_load)
            log.info("speakers.campp.warmed")
        except Exception as exc:
            log.warning("speakers.campp.warmup_failed", error=type(exc).__name__)

    # 预热 FunASR SPK 引擎（统一 ASR + 说话人分离）
    try:
        from meet_transcribe.core.funasr_adapter import init_spk_engine

        await asyncio.to_thread(init_spk_engine, cfg.asr.device)
        log.info("funasr.spk_engine.warmed")
    except Exception as exc:
        log.warning("funasr.engine.warmup_failed", error=type(exc).__name__)

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
app.include_router(speakers_routes.router, prefix="/v1/speakers")
app.include_router(transcribe_routes.router, prefix="/v1")
app.include_router(ws_routes.router, prefix="/v1")

_demo_dir = _repo_root() / "web-demo"
if _demo_dir.exists():
    app.mount("/demo", StaticFiles(directory=str(_demo_dir), html=True), name="demo")


@app.get("/health")
def health() -> dict[str, str]:
    """liveness: 进程是否存活。"""
    return {"status": "ok", "version": __version__}


import datetime as _dt
_started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()


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
    return {"status": overall, "checks": checks, "started_at": _started_at}


@app.get("/metrics")
def metrics() -> Response:
    body, ctype = render_latest()
    return Response(content=body, media_type=ctype)


def run_cli() -> int:
    """`meet-transcribe` 入口。"""
    import os
    import uvicorn

    cfg = load_config()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    ssl_keyfile = os.environ.get("MT_DEV_HTTPS_KEY")
    ssl_certfile = os.environ.get("MT_DEV_HTTPS_CERT")

    uvicorn.run(
        "meet_transcribe.api.app:app",
        host=os.environ.get("MT_SERVER_HOST", cfg.server.host),
        port=int(os.environ.get("MT_SERVER_PORT", str(cfg.server.port))),
        workers=cfg.server.workers,
        log_level=cfg.observability.log_level.lower(),
        loop="asyncio" if sys.platform == "win32" else "auto",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
