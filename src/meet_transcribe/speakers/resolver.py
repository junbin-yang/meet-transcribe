"""实时流式 speaker resolver。

每个 speaker_id 累计 ≥3s 语音后触发 CAM++ embedding + pgvector 匹配。
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker

from meet_transcribe.observability.logging import get_logger
from meet_transcribe.speakers.embedding import (
    EMBEDDING_DIM,
    DecodedAudio,
    QualityRejected,
    compute_embedding,
)
from meet_transcribe.speakers.matcher import MatchResult, match

log = get_logger(__name__)


class SpeakerResolver:
    SAMPLE_RATE = 16000
    MIN_TRIGGER_SECONDS = 3.0

    def __init__(
        self,
        tenant_id: uuid.UUID,
        threshold: float,
        embedding_model: str,
        session_factory: async_sessionmaker[Any],
        ring_buffer_seconds: float = 30.0,
        min_trigger_seconds: float = MIN_TRIGGER_SECONDS,
    ):
        self.tenant_id = tenant_id
        self.threshold = threshold
        self.embedding_model = embedding_model
        self._session_factory = session_factory
        self._ring_buffer_seconds = float(ring_buffer_seconds)
        self._min_trigger_seconds = float(min_trigger_seconds)

        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_t0 = 0.0
        self._total_seconds = 0.0
        self._segments: dict[int, list[list[float]]] = defaultdict(list)
        self._duration: dict[int, float] = defaultdict(float)
        self._resolved: dict[int, MatchResult | None] = {}
        self._pending: set[int] = set()

    @property
    def total_audio_seconds(self) -> float:
        return self._total_seconds

    def feed_audio(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        try:
            samples = (
                np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            )
        except ValueError:
            log.warning("speakers.resolver.bad_pcm", nbytes=len(pcm_bytes))
            return
        if samples.size == 0:
            return
        self._buf = np.concatenate([self._buf, samples])
        self._total_seconds += samples.size / self.SAMPLE_RATE

        max_samples = int(self._ring_buffer_seconds * self.SAMPLE_RATE)
        if self._buf.size > max_samples:
            drop = self._buf.size - max_samples
            self._buf = self._buf[drop:]
            self._buf_t0 += drop / self.SAMPLE_RATE

    def note_segment(self, speaker: int, start: float, end: float) -> bool:
        if speaker <= 0 or end <= start:
            return False
        segs = self._segments[speaker]
        if segs and start <= segs[-1][1]:
            old_end = segs[-1][1]
            new_end = max(old_end, end)
            added = max(0.0, new_end - old_end)
            self._duration[speaker] += added
            segs[-1][1] = new_end
        else:
            segs.append([start, end])
            self._duration[speaker] += end - start

        should = (
            speaker not in self._resolved
            and speaker not in self._pending
            and self._duration[speaker] >= self._min_trigger_seconds
        )
        if should:
            log.info("resolver.note_segment.trigger", speaker=speaker,
                     duration=round(self._duration[speaker], 2))
        return should

    def get(self, speaker: int) -> dict[str, Any] | None:
        res = self._resolved.get(speaker)
        if res is None or not res.matched:
            return None
        return {
            "id": str(res.speaker_id),
            "name": res.name,
            "score": round(float(res.score), 4),
        }

    def _collect_audio(self, speaker: int) -> np.ndarray | None:
        if speaker not in self._segments:
            return None
        chunks: list[np.ndarray] = []
        for start, end in self._segments[speaker]:
            s_idx = int(max(0.0, (start - self._buf_t0) * self.SAMPLE_RATE))
            e_idx = int(
                min(float(self._buf.size), (end - self._buf_t0) * self.SAMPLE_RATE)
            )
            if e_idx > s_idx:
                chunks.append(self._buf[s_idx:e_idx])
        if not chunks:
            return None
        return np.concatenate(chunks).astype(np.float32, copy=False)

    async def trigger(self, speaker: int) -> dict[str, Any] | None:
        if speaker in self._resolved:
            return self.get(speaker)
        if speaker in self._pending:
            return None
        self._pending.add(speaker)
        try:
            audio = self._collect_audio(speaker)
            if audio is None or audio.size / self.SAMPLE_RATE < self._min_trigger_seconds:
                self._pending.discard(speaker)
                return None

            decoded = DecodedAudio(
                samples=audio, duration_s=float(audio.size / self.SAMPLE_RATE)
            )
            try:
                emb = await asyncio.to_thread(
                    compute_embedding,
                    decoded,
                    min_sample_seconds=self._min_trigger_seconds,
                    min_snr_db=0.0,
                )
            except QualityRejected as e:
                log.info("speakers.resolver.quality_rejected",
                         speaker=speaker, reason=e.reason)
                self._pending.discard(speaker)
                return None

            if emb.embedding.shape != (EMBEDDING_DIM,):
                log.warning("speakers.resolver.bad_embedding_shape",
                            shape=tuple(emb.embedding.shape))
                self._pending.discard(speaker)
                return None

            async with self._session_factory() as db:
                result = await match(
                    db,
                    tenant_id=self.tenant_id,
                    query=emb.embedding,
                    threshold=self.threshold,
                )

            if result.matched:
                self._resolved[speaker] = result
            log.info("speakers.resolver.resolved",
                     speaker=speaker, matched=result.matched,
                     score=round(float(result.score), 4) if result.score != float("-inf") else None)
            return self.get(speaker)
        except Exception:
            log.exception("speakers.resolver.trigger_failed", speaker=speaker)
            self._pending.discard(speaker)
            return None
        finally:
            self._pending.discard(speaker)
