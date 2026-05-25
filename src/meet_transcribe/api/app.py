"""FastAPI 入口骨架。M1 会扩展鉴权、ticket、WebSocket 等路由。"""

from __future__ import annotations

import sys

from fastapi import FastAPI

from meet_transcribe import __version__

app = FastAPI(
    title="meet-transcribe",
    version=__version__,
    description="Meeting real-time speech-to-text backend (B2B private deployment)",
)


@app.get("/health")
def health() -> dict[str, str]:
    """liveness: 进程是否存活。"""
    return {"status": "ok", "version": __version__}


@app.get("/ready")
def ready() -> dict[str, str]:
    """readiness: 模型已加载 + DB 连通 + GPU 可分配。M0 占位。"""
    return {"status": "loading"}


def run_cli() -> int:
    """`meet-transcribe` 入口。"""
    import uvicorn

    from meet_transcribe.config.loader import load_config

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
