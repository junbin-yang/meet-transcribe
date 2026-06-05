"""声纹匹配：pgvector 余弦近邻 + 进程级 LRU 缓存。

设计要点（v2 §4.6）：
  - HNSW(m=16, ef_construction=64) 索引已在 init_schema.sql 第 43-45 行建好。
  - 余弦距离 = 1 - cosine_sim；pgvector 的 `<=>` 运算符就是余弦距离。
  - 仅当 sim >= match_threshold(默认 0.65) 才视为命中。
  - LRU 缓存查询结果，key=(tenant_id, embedding_bytes)；离线注册阶段命中率低，
    主要为识别实时流准备（流式 tracker 会聚合 cluster 后再查询，命中率较高）。

本模块对外纯函数，不持有 DB session；调用方注入 AsyncSession。
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meet_transcribe.db.models import Speaker
from meet_transcribe.observability.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class MatchResult:
    speaker_id: uuid.UUID | None
    name: str | None
    score: float  # cosine similarity in [-1, 1]; -inf 表示无候选
    threshold: float

    @property
    def matched(self) -> bool:
        return self.speaker_id is not None


class _LRU:
    """单线程 / 单事件循环的 LRU；O(1) get/put。"""

    def __init__(self, maxsize: int) -> None:
        self._cache: OrderedDict[Any, MatchResult] = OrderedDict()
        self._maxsize = max(1, int(maxsize))

    def get(self, key: Any) -> MatchResult | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: Any, val: MatchResult) -> None:
        self._cache[key] = val
        self._cache.move_to_end(key)
        if len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


_lru: _LRU | None = None


def _get_cache(maxsize: int) -> _LRU:
    global _lru
    if _lru is None or _lru._maxsize != maxsize:
        _lru = _LRU(maxsize)
    return _lru


def _cache_key(tenant_id: uuid.UUID, query: np.ndarray) -> tuple[uuid.UUID, bytes]:
    return (tenant_id, query.astype(np.float32, copy=False).tobytes())


async def match(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    query: np.ndarray,
    threshold: float,
    cache_size: int = 1024,
) -> MatchResult:
    """对当前 tenant 的非删除声纹做 top-1 余弦近邻匹配。

    pgvector 的 `<=>` 是余弦距离 (0 = identical)。我们用 `cosine_distance()` 表达。
    分数 = 1 - distance；阈值在余弦相似度空间比较。
    """
    if query.shape != (192,):
        raise ValueError(f"query embedding must be shape (192,), got {query.shape}")
    q = query.astype(np.float32, copy=False)

    cache = _get_cache(cache_size)
    key = _cache_key(tenant_id, q)
    cached = cache.get(key)
    if cached is not None:
        return cached

    stmt = (
        select(Speaker.id, Speaker.name, Speaker.embedding.cosine_distance(q).label("dist"))
        .where(Speaker.tenant_id == tenant_id, Speaker.deleted_at.is_(None))
        .order_by("dist")
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        result = MatchResult(speaker_id=None, name=None, score=float("-inf"), threshold=threshold)
    else:
        speaker_id, name, dist = row
        score = float(1.0 - dist)
        if score >= threshold:
            result = MatchResult(
                speaker_id=speaker_id, name=name, score=score, threshold=threshold
            )
        else:
            result = MatchResult(
                speaker_id=None, name=None, score=score, threshold=threshold
            )

    cache.put(key, result)
    return result


def reset_cache() -> None:
    """测试 / 注册后失效缓存。"""
    if _lru is not None:
        _lru.clear()
