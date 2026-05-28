"""speakers.matcher 的纯函数测试：LRU + MatchResult。"""

from __future__ import annotations

import uuid

import numpy as np

from meet_transcribe.speakers.matcher import MatchResult, _LRU, _cache_key, reset_cache


def test_match_result_matched_property() -> None:
    sid = uuid.uuid4()
    hit = MatchResult(speaker_id=sid, name="alice", score=0.82, threshold=0.65)
    miss = MatchResult(speaker_id=None, name=None, score=0.4, threshold=0.65)
    assert hit.matched is True
    assert miss.matched is False


def test_lru_evicts_oldest() -> None:
    cache: _LRU = _LRU(maxsize=2)
    cache.put("a", MatchResult(None, None, 0.1, 0.5))
    cache.put("b", MatchResult(None, None, 0.2, 0.5))
    cache.put("c", MatchResult(None, None, 0.3, 0.5))

    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None
    assert len(cache) == 2


def test_lru_move_to_end_on_get() -> None:
    cache: _LRU = _LRU(maxsize=2)
    cache.put("a", MatchResult(None, None, 0.1, 0.5))
    cache.put("b", MatchResult(None, None, 0.2, 0.5))
    cache.get("a")  # promote a
    cache.put("c", MatchResult(None, None, 0.3, 0.5))  # evicts b now

    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_cache_key_is_stable_for_same_vector() -> None:
    tid = uuid.uuid4()
    v1 = np.arange(192, dtype=np.float32)
    v2 = np.arange(192, dtype=np.float32)
    assert _cache_key(tid, v1) == _cache_key(tid, v2)


def test_cache_key_differs_for_different_tenants() -> None:
    a = uuid.uuid4()
    b = uuid.uuid4()
    v = np.arange(192, dtype=np.float32)
    assert _cache_key(a, v) != _cache_key(b, v)


def test_reset_cache_idempotent_when_uninitialized() -> None:
    """reset_cache() 在缓存未懒初始化时也不能崩。"""
    reset_cache()
    reset_cache()
