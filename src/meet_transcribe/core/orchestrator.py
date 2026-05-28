"""会话编排器 — FunASR 单引擎流式版。

使用 FunASR 组件模型(paraformer-zh + VAD + SPK + Punc) 统一处理 ASR + 说话人分离。
每 2s 跑一次模型，每 500ms 推送缓存结果到前端。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from meet_transcribe.core.funasr_adapter import (
    FunASRSpec,
    init_spk_engine,
)
from meet_transcribe.observability.logging import get_logger
from meet_transcribe.observability.metrics import (
    AUDIO_INPUT_SECONDS,
    WORDS_EMITTED,
)
from meet_transcribe.speakers.resolver import SpeakerResolver

log = get_logger(__name__)


def _build_text_postprocessor(mode: str) -> Any:
    if mode == "none" or not mode:
        return lambda s: s
    try:
        from opencc import OpenCC
        converter = OpenCC(mode)
        return lambda text: text if not text else converter.convert(text)
    except (ImportError, FileNotFoundError):
        log.warning("opencc unavailable", mode=mode)
        return lambda s: s


@dataclass
class StartFrame:
    language: str = "auto"
    hotwords: list[str] = field(default_factory=list)
    session_hint: str | None = None
    speaker_set: str | None = None
    model: str | None = None  # override config model tier


@dataclass
class OutgoingEvent:
    type: str
    payload: dict[str, Any]


class SessionOrchestrator:
    SAMPLE_RATE = 16000
    FLUSH_INTERVAL_S = 0.5
    MODEL_INTERVAL_S = 2.0

    def __init__(
        self,
        tenant_id: uuid.UUID,
        engine_spec: FunASRSpec,
        start: StartFrame,
        *,
        resolver: SpeakerResolver | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.session_id = uuid.uuid4()
        self.start = start
        self.spec = engine_spec
        self._seq = 0
        self._stopped = False
        self._tenant_label = str(tenant_id)
        self._t0: float | None = None
        self._postprocess = _build_text_postprocessor(engine_spec.language)
        self._resolver = resolver
        self._outgoing: asyncio.Queue[OutgoingEvent | None] = asyncio.Queue()
        self._resolver_tasks: set[asyncio.Task[Any]] = set()

        self._audio_buffer = bytearray()
        self._last_flush = 0.0
        self._last_sent = ""
        self._last_model_run_audio_s = 0.0
        self._model_running = False

        # Cached results from last model run
        self._cached_lines: list[dict[str, Any]] = []
        self._cached_text = ""

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
            self._last_flush = self._t0
        seconds = len(audio_chunk) / (self.SAMPLE_RATE * 2)
        AUDIO_INPUT_SECONDS.labels(tenant_id=self._tenant_label).inc(seconds)
        if self._resolver is not None:
            self._resolver.feed_audio(audio_chunk)
        self._audio_buffer.extend(audio_chunk)
        if time.monotonic() - self._last_flush >= self.FLUSH_INTERVAL_S:
            await self._flush()

    async def stream(self) -> AsyncIterator[OutgoingEvent]:
        while True:
            event = await self._outgoing.get()
            if event is None:
                return
            yield event

    async def _flush(self) -> None:
        if len(self._audio_buffer) == 0:
            return
        self._last_flush = time.monotonic()

        buf_audio_s = len(self._audio_buffer) / (self.SAMPLE_RATE * 2)

        # Trigger model run every MODEL_INTERVAL_S
        if (buf_audio_s - self._last_model_run_audio_s >= self.MODEL_INTERVAL_S) and not self._model_running:
            self._last_model_run_audio_s = buf_audio_s
            self._model_running = True
            asyncio.create_task(self._run_model(buf_audio_s))

        # Push cached results to frontend
        if not self._cached_lines or self._cached_text == self._last_sent:
            return
        self._last_sent = self._cached_text

        await self._outgoing.put(
            OutgoingEvent(
                type="partial",
                payload={
                    "text": self._cached_text,
                    "lines": self._cached_lines,
                    "session_id": str(self.session_id),
                    "seq": self._next_seq(),
                },
            )
        )

    async def _run_model(self, buf_audio_s: float) -> None:
        """Run SPK model on full accumulated audio, update cached results."""
        try:
            raw = bytes(self._audio_buffer)
            audio_f32 = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

            loop = asyncio.get_running_loop()
            asr_model = self.start.model or self.spec.model
            engine = init_spk_engine(self.spec.device, asr_model=asr_model)
            result = await loop.run_in_executor(
                None,
                lambda: engine.generate(
                    input=audio_f32,
                    input_fs=self.SAMPLE_RATE,
                    language=self.start.language,
                    batch_size_s=300,
                ),
            )

            if isinstance(result, list) and result:
                r0 = result[0]
                if isinstance(r0, dict):
                    si = r0.get("sentence_info", []) or []
                    if si:
                        lines: list[dict[str, Any]] = []
                        text_parts: list[str] = []
                        for s in si:
                            if not isinstance(s, dict):
                                continue
                            si_text = self._postprocess(str(s.get("text", "")))
                            if not si_text.strip():
                                continue
                            text_parts.append(si_text)
                            spk = int(s.get("spk", 0)) + 1
                            lines.append({
                                "text": si_text,
                                "speaker": spk,
                                "start": float(s.get("start", 0)) / 1000.0,
                                "end": float(s.get("end", 0)) / 1000.0,
                            })

                        if lines:
                            # Voiceprint resolver: match known speakers
                            if self._resolver is not None:
                                unique_speakers = {l["speaker"] for l in lines}
                                for spk in unique_speakers:
                                    for line in lines:
                                        if line["speaker"] == spk and line.get("start"):
                                            self._resolver.note_segment(
                                                spk, float(line["start"]), float(line["end"])
                                            )
                                    if (spk not in self._resolver._resolved
                                            and spk not in self._resolver._pending
                                            and self._resolver._duration.get(spk, 0.0) >= 3.0):
                                        t = asyncio.create_task(self._resolver.trigger(spk))
                                        self._resolver_tasks.add(t)
                                        t.add_done_callback(self._resolver_tasks.discard)
                                    resolved = self._resolver.get(spk)
                                    if resolved:
                                        for line in lines:
                                            if line["speaker"] == spk:
                                                line["speaker_resolved"] = resolved

                            self._cached_lines = lines
                            self._cached_text = "".join(text_parts)
                            log.info(
                                "model.run.done",
                                buf_audio_s=round(buf_audio_s, 1),
                                sentences=len(lines),
                                speakers=len({l["speaker"] for l in lines}),
                            )
        except Exception:
            log.exception("model.run.failed")
        finally:
            self._model_running = False

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        # Run model one final time, then push last cached result
        buf_audio_s = len(self._audio_buffer) / (self.SAMPLE_RATE * 2)
        if not self._model_running:
            await self._run_model(buf_audio_s)
        if self._cached_lines and self._cached_text != self._last_sent:
            self._last_sent = self._cached_text
            await self._outgoing.put(
                OutgoingEvent(
                    type="partial",
                    payload={
                        "text": self._cached_text,
                        "lines": self._cached_lines,
                        "session_id": str(self.session_id),
                        "seq": self._next_seq(),
                    },
                )
            )
        await self._outgoing.put(None)


def emit_final_metrics(tenant_id: str, words: int) -> None:
    WORDS_EMITTED.labels(tenant_id=tenant_id).inc(words)
