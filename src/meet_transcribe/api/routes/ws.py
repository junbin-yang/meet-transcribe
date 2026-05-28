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
from meet_transcribe.core.funasr_adapter import FunASRSpec
from meet_transcribe.db.session import get_session_factory
from meet_transcribe.observability.logging import get_logger
from meet_transcribe.observability.metrics import (
    ACTIVE_SESSIONS,
    AUTH_FAILURES,
    ERRORS_TOTAL,
    SESSIONS_TOTAL,
)
from meet_transcribe.speakers.resolver import SpeakerResolver

router = APIRouter()
log = get_logger(__name__)

WS_CLOSE_AUTH_FAIL = status.WS_1008_POLICY_VIOLATION


def _engine_spec_from_cfg() -> FunASRSpec:
    cfg = load_config()
    return FunASRSpec(
        model=cfg.asr.model,
        device=cfg.asr.device,
        language=cfg.asr.language,
    )


def _build_resolver_if_enabled(
    tenant_id: Any, cfg: Any
) -> SpeakerResolver | None:
    if not cfg.diarization.enabled:
        log.info("ws.resolver.disabled")
        return None
    try:
        factory = get_session_factory()
    except RuntimeError:
        log.warning("ws.resolver.db_uninitialized")
        return None
    log.info("ws.resolver.created", tenant_id=str(tenant_id))
    return SpeakerResolver(
        tenant_id=tenant_id,
        threshold=cfg.speakers.match_threshold,
        embedding_model=cfg.speakers.embedding_model,
        session_factory=factory,
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
            resolver=_build_resolver_if_enabled(tenant_id, cfg),
        )

        send_task = asyncio.create_task(_pump_outgoing(ws, orchestrator))
        try:
            await _pump_incoming(ws, orchestrator)
        finally:
            # 客户端发了 end（或断开），先停上游让 generator 自然走完最后一批
            # partial，再给 send_task 几秒把队列排干，最后兜底 cancel。
            with _Suppress():
                await orchestrator.stop()
            try:
                await asyncio.wait_for(send_task, timeout=5)
            except asyncio.TimeoutError:
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
        language=str(data.get("language") or "auto"),
        hotwords=list(data.get("hotwords") or []),
        session_hint=data.get("session_hint"),
        speaker_set=data.get("speaker_set"),
        model=data.get("model"),
    )


async def _pump_incoming(ws: WebSocket, orch: SessionOrchestrator) -> None:
    log.info("pump.incoming.start", session_id=str(orch.session_id))
    while True:
        msg = await ws.receive()
        kind = msg.get("type")
        if kind == "websocket.disconnect":
            log.info("pump.incoming.disconnect")
            return
        if kind != "websocket.receive":
            continue
        if (b := msg.get("bytes")) is not None:
            log.info("pump.incoming.bytes", nbytes=len(b))
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
    try:
        async for evt in orch.stream():
            await ws.send_text(
                json.dumps({"type": evt.type, **evt.payload}, ensure_ascii=False)
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("ws pump_outgoing crashed")
        raise


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
