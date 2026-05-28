"""开发环境启动脚本 — 正确处理 Windows event loop。"""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from meet_transcribe.api.app import run_cli

sys.exit(run_cli())
