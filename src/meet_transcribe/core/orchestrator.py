"""会话编排器：每个 WebSocket 连接独立一个实例。

跨租户隔离来自"每会话独立 AudioProcessor"。这是 v2 设计 C1 的核心兑现。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from meet_transcribe.core.whisperlivekit_adapter import (
    EngineSpec,
    init_engine,
    make_session_processor,
)
from meet_transcribe.observability.logging import get_logger
from meet_transcribe.observability.metrics import (
    AUDIO_INPUT_SECONDS,
    INFERENCE_LATENCY,
    WORDS_EMITTED,
)

log = get_logger(__name__)


@dataclass
class StartFrame:
    language: str = "zh"
    hotwords: list[str] = field(default_factory=list)
    session_hint: str | None = None
    speaker_set: str | None = None


@dataclass
class OutgoingEvent:
    type: str  # "partial" | "final" | "error" | "ping"
    payload: dict[str, Any]


class SessionOrchestrator:
    SAMPLE_RATE = 16000
    BYTES_PER_SAMPLE = 2

    def __init__(
        self,
        tenant_id: uuid.UUID,
        engine_spec: EngineSpec,
        start: StartFrame,
    ) -> None:
        self.tenant_id = tenant_id
        self.session_id = uuid.uuid4()
        self.start = start
        self.spec = engine_spec
        self._engine = init_engine(engine_spec)
        self._processor = make_session_processor(self._engine, language=start.language)
        self._seq = 0
        self._stable_until = 0.0
        self._stopped = False
        self._tenant_label = str(tenant_id)
        self._t0: float | None = None

    @property
    def started_audio_seconds(self) -> float:
        return 0.0 if self._t0 is None else max(0.0, time.monotonic() - self._t0)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def feed(self, audio_chunk: bytes) -> None:
        if self._stopped:
            return
        if self._t0 is None:
            self._t0 = time.monotonic()
        seconds = len(audio_chunk) / (self.SAMPLE_RATE * self.BYTES_PER_SAMPLE)
        AUDIO_INPUT_SECONDS.labels(tenant_id=self._tenant_label).inc(seconds)

        process_audio = getattr(self._processor, "process_audio", None)
        if process_audio is None:
            return
        await process_audio(audio_chunk)

    async def stream(self) -> AsyncIterator[OutgoingEvent]:
        """把 AudioProcessor 的输出转换成对外协议事件。

        M1 这里给出骨架，真正订阅 upstream queue 在 M2 完成。
        """
        while not self._stopped:
            await asyncio.sleep(20)
            yield OutgoingEvent(type="ping", payload={"ts": datetime.now(UTC).isoformat()})

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        stop_fn = getattr(self._processor, "stop", None)
        if stop_fn is not None:
            try:
                await stop_fn()
            except Exception:
                log.exception("processor.stop failed", session_id=str(self.session_id))


def emit_final_metrics(tenant_id: str, words: int) -> None:
    WORDS_EMITTED.labels(tenant_id=tenant_id).inc(words)


def observe_inference(latency_seconds: float, kind: str) -> None:
    INFERENCE_LATENCY.labels(kind=kind).observe(latency_seconds)
