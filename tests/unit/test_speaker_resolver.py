"""SpeakerResolver 单元测试：环形缓冲累计 / 触发阈值 / 缓存 / 多说话人。

不接 DB / 不加载 ECAPA 模型：用 monkeypatch 替换 compute_embedding 与 match。
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
import pytest

from meet_transcribe.speakers import resolver as resolver_module
from meet_transcribe.speakers.embedding import EMBEDDING_DIM, EmbeddingResult
from meet_transcribe.speakers.matcher import MatchResult
from meet_transcribe.speakers.resolver import SpeakerResolver


def _pcm_bytes(seconds: float, sr: int = 16000) -> bytes:
    n = int(seconds * sr)
    return (np.zeros(n, dtype=np.int16)).tobytes()


def _fake_factory() -> Any:
    @asynccontextmanager
    async def _ctx():
        yield object()

    return _ctx


def _make_resolver(**overrides: Any) -> SpeakerResolver:
    defaults: dict[str, Any] = {
        "tenant_id": uuid.uuid4(),
        "threshold": 0.65,
        "embedding_model": "fake/ecapa",
        "session_factory": _fake_factory(),
    }
    defaults.update(overrides)
    return SpeakerResolver(**defaults)


def test_feed_audio_accumulates_total_seconds() -> None:
    r = _make_resolver()
    r.feed_audio(_pcm_bytes(1.0))
    r.feed_audio(_pcm_bytes(2.0))
    assert r.total_audio_seconds == pytest.approx(3.0, abs=1e-3)


def test_feed_audio_handles_empty_and_bad_pcm() -> None:
    r = _make_resolver()
    r.feed_audio(b"")
    r.feed_audio(b"\x00")  # 奇数字节 → np.frombuffer 抛 ValueError
    assert r.total_audio_seconds == 0.0


def test_ring_buffer_drops_old_samples() -> None:
    r = _make_resolver(ring_buffer_seconds=2.0)
    r.feed_audio(_pcm_bytes(3.0))
    assert r._buf.size == 2 * SpeakerResolver.SAMPLE_RATE
    assert r._buf_t0 == pytest.approx(1.0, abs=1e-3)


def test_note_segment_triggers_only_once_at_threshold() -> None:
    r = _make_resolver(min_trigger_seconds=3.0)
    assert r.note_segment(1, 0.0, 1.5) is False
    assert r.note_segment(1, 1.5, 2.5) is False
    assert r.note_segment(1, 2.5, 3.5) is True
    r._pending.add(1)
    assert r.note_segment(1, 3.5, 5.0) is False


def test_note_segment_overlap_merge_does_not_double_count() -> None:
    r = _make_resolver(min_trigger_seconds=10.0)
    r.note_segment(1, 0.0, 2.0)
    r.note_segment(1, 1.0, 3.0)
    assert r._duration[1] == pytest.approx(3.0, abs=1e-3)


def test_note_segment_rejects_invalid_range() -> None:
    r = _make_resolver()
    assert r.note_segment(1, 5.0, 5.0) is False
    assert r.note_segment(1, 5.0, 4.0) is False
    assert r.note_segment(0, 0.0, 3.5) is False
    assert r.note_segment(-1, 0.0, 3.5) is False
    assert r._duration == {}


def test_get_returns_none_for_unresolved() -> None:
    r = _make_resolver()
    assert r.get(1) is None
    r._resolved[1] = None
    assert r.get(1) is None
    r._resolved[2] = MatchResult(
        speaker_id=None, name=None, score=0.3, threshold=0.65
    )
    assert r.get(2) is None


def test_get_returns_payload_for_resolved() -> None:
    r = _make_resolver()
    sid = uuid.uuid4()
    r._resolved[1] = MatchResult(
        speaker_id=sid, name="Alice", score=0.812345, threshold=0.65
    )
    payload = r.get(1)
    assert payload == {"id": str(sid), "name": "Alice", "score": 0.8123}


@pytest.mark.asyncio
async def test_trigger_caches_unresolved_when_no_audio() -> None:
    r = _make_resolver()
    r.note_segment(1, 0.0, 4.0)
    out = await r.trigger(1)
    assert out is None
    out2 = await r.trigger(1)
    assert out2 is None


@pytest.mark.asyncio
async def test_trigger_calls_compute_and_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make_resolver()
    r.feed_audio(_pcm_bytes(4.0))
    r.note_segment(1, 0.0, 4.0)

    fake_embedding = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    fake_embedding[0] = 1.0
    sid = uuid.uuid4()

    def fake_compute(audio: Any, **kwargs: Any) -> EmbeddingResult:
        return EmbeddingResult(embedding=fake_embedding, duration_s=4.0, snr_db=30.0)

    async def fake_match(db: Any, **kwargs: Any) -> MatchResult:
        return MatchResult(
            speaker_id=sid, name="Bob", score=0.91, threshold=kwargs["threshold"]
        )

    monkeypatch.setattr(resolver_module, "compute_embedding", fake_compute)
    monkeypatch.setattr(resolver_module, "match", fake_match)

    payload = await r.trigger(1)
    assert payload == {"id": str(sid), "name": "Bob", "score": 0.91}

    calls = {"n": 0}

    def fake_compute2(audio: Any, **kwargs: Any) -> EmbeddingResult:
        calls["n"] += 1
        return EmbeddingResult(embedding=fake_embedding, duration_s=4.0, snr_db=30.0)

    monkeypatch.setattr(resolver_module, "compute_embedding", fake_compute2)
    payload2 = await r.trigger(1)
    assert payload2 == payload
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_trigger_caches_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _make_resolver()
    r.feed_audio(_pcm_bytes(4.0))
    r.note_segment(1, 0.0, 4.0)

    fake_embedding = np.zeros(EMBEDDING_DIM, dtype=np.float32)

    def fake_compute(audio: Any, **kwargs: Any) -> EmbeddingResult:
        return EmbeddingResult(embedding=fake_embedding, duration_s=4.0, snr_db=10.0)

    async def fake_match(db: Any, **kwargs: Any) -> MatchResult:
        return MatchResult(
            speaker_id=None, name=None, score=0.3, threshold=kwargs["threshold"]
        )

    monkeypatch.setattr(resolver_module, "compute_embedding", fake_compute)
    monkeypatch.setattr(resolver_module, "match", fake_match)

    assert await r.trigger(1) is None
    assert 1 not in r._resolved


@pytest.mark.asyncio
async def test_multiple_speakers_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = _make_resolver()
    r.feed_audio(_pcm_bytes(8.0))
    r.note_segment(1, 0.0, 3.5)
    r.note_segment(2, 3.5, 7.0)

    sid1, sid2 = uuid.uuid4(), uuid.uuid4()
    emb = np.zeros(EMBEDDING_DIM, dtype=np.float32)

    def fake_compute(audio: Any, **kwargs: Any) -> EmbeddingResult:
        return EmbeddingResult(embedding=emb, duration_s=3.5, snr_db=20.0)

    state = {"n": 0}

    async def fake_match(db: Any, **kwargs: Any) -> MatchResult:
        idx = state["n"]
        state["n"] += 1
        if idx == 0:
            return MatchResult(sid1, "A", 0.8, kwargs["threshold"])
        return MatchResult(sid2, "B", 0.75, kwargs["threshold"])

    monkeypatch.setattr(resolver_module, "compute_embedding", fake_compute)
    monkeypatch.setattr(resolver_module, "match", fake_match)

    p1 = await r.trigger(1)
    p2 = await r.trigger(2)
    assert p1 is not None and p1["name"] == "A"
    assert p2 is not None and p2["name"] == "B"


@pytest.mark.asyncio
async def test_trigger_swallows_quality_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meet_transcribe.speakers.embedding import QualityRejected

    r = _make_resolver()
    r.feed_audio(_pcm_bytes(4.0))
    r.note_segment(1, 0.0, 4.0)

    def fake_compute(audio: Any, **kwargs: Any) -> EmbeddingResult:
        raise QualityRejected("too_quiet", {"snr_db": 2.0})

    monkeypatch.setattr(resolver_module, "compute_embedding", fake_compute)

    assert await r.trigger(1) is None
    assert 1 not in r._resolved
