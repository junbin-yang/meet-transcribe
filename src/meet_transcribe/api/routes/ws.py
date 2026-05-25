"""GET /v1/ws/transcribe — 实时转写 WebSocket 入口。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, status

from meet_transcribe.auth.ticket import (
    TicketError,
    TicketExpired,
    TicketInvalid,
    TicketReplayed,
    verify,
)
from meet_transcribe.config.loader import load_config
from meet_transcribe.core.orchestrator import SessionOrchestrator, StartFrame
from meet_transcribe.core.whisperlivekit_adapter import EngineSpec
from meet_transcribe.observability.logging import get_logger
from meet_transcribe.observability.metrics import (
    ACTIVE_SESSIONS,
    AUTH_FAILURES,
    ERRORS_TOTAL,
    SESSIONS_TOTAL,
)

router = APIRouter()
log = get_logger(__name__)

WS_CLOSE_AUTH_FAIL = status.WS_1008_POLICY_VIOLATION


def _engine_spec_from_cfg() -> EngineSpec:
    cfg = load_config()
    return EngineSpec(
        model=cfg.asr.model,
        language=cfg.asr.language,
        compute_type=cfg.asr.compute_type,
        device=cfg.asr.device,
        diarization=cfg.diarization.enabled,
        vad=cfg.streaming.vad_enabled,
        pcm_input=True,
    )


@router.websocket("/ws/transcribe")
async def ws_transcribe(ws: WebSocket, ticket: str = Query("")) -> None:
    cfg = load_config()
    secret = cfg.secrets.server_secret.get_secret_value().encode("utf-8")

    if not secret:
        await ws.close(code=WS_CLOSE_AUTH_FAIL, reason="server not initialized")
        return

    try:
        tenant_id = verify(ticket, secret)
    except TicketExpired:
        AUTH_FAILURES.labels(reason="ticket_expired").inc()
        await ws.close(code=WS_CLOSE_AUTH_FAIL, reason="AUTH_FAIL")
        return
    except TicketReplayed:
        AUTH_FAILURES.labels(reason="ticket_replay").inc()
        await ws.close(code=WS_CLOSE_AUTH_FAIL, reason="AUTH_FAIL")
        return
    except (TicketInvalid, TicketError):
        AUTH_FAILURES.labels(reason="ticket_invalid").inc()
        await ws.close(code=WS_CLOSE_AUTH_FAIL, reason="AUTH_FAIL")
        return

    await ws.accept()
    tenant_label = str(tenant_id)
    ACTIVE_SESSIONS.labels(tenant_id=tenant_label).inc()

    orchestrator: SessionOrchestrator | None = None
    outcome = "ok"

    try:
        first = await asyncio.wait_for(ws.receive(), timeout=10)
        start_frame = _parse_start_frame(first)
        if start_frame is None:
            await _send_error(ws, "VALIDATION_FAILED", "expected start control frame")
            return

        orchestrator = SessionOrchestrator(
            tenant_id=tenant_id,
            engine_spec=_engine_spec_from_cfg(),
            start=start_frame,
        )

        send_task = asyncio.create_task(_pump_outgoing(ws, orchestrator))
        try:
            await _pump_incoming(ws, orchestrator)
        finally:
            send_task.cancel()
            with _Suppress():
                await send_task
    except asyncio.TimeoutError:
        outcome = "timeout"
        await _send_error(ws, "VALIDATION_FAILED", "no start frame")
    except Exception:
        outcome = "internal"
        log.exception("ws session crashed", tenant_id=tenant_label)
        await _send_error(ws, "INTERNAL", "session aborted")
    finally:
        if orchestrator is not None:
            await orchestrator.stop()
        ACTIVE_SESSIONS.labels(tenant_id=tenant_label).dec()
        SESSIONS_TOTAL.labels(tenant_id=tenant_label, outcome=outcome).inc()
        with _Suppress():
            await ws.close()


def _parse_start_frame(received: dict[str, Any]) -> StartFrame | None:
    if received.get("type") != "websocket.receive":
        return None
    text = received.get("text")
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if data.get("type") != "start":
        return None
    return StartFrame(
        language=str(data.get("language") or "zh"),
        hotwords=list(data.get("hotwords") or []),
        session_hint=data.get("session_hint"),
        speaker_set=data.get("speaker_set"),
    )


async def _pump_incoming(ws: WebSocket, orch: SessionOrchestrator) -> None:
    while True:
        msg = await ws.receive()
        kind = msg.get("type")
        if kind == "websocket.disconnect":
            return
        if kind != "websocket.receive":
            continue
        if (b := msg.get("bytes")) is not None:
            await orch.feed(b)
            continue
        if (t := msg.get("text")) is not None:
            try:
                payload = json.loads(t)
            except json.JSONDecodeError:
                continue
            if payload.get("type") == "end":
                return


async def _pump_outgoing(ws: WebSocket, orch: SessionOrchestrator) -> None:
    async for evt in orch.stream():
        await ws.send_text(json.dumps({"type": evt.type, **evt.payload}, ensure_ascii=False))


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    ERRORS_TOTAL.labels(code=code).inc()
    with _Suppress():
        await ws.send_text(json.dumps({"type": "error", "code": code, "message": message}))


class _Suppress:
    """contextmanager 用于忽略 close/send 的二次报错。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> bool:
        return True
