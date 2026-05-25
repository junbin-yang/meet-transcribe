"""一次性 ticket：HTTP 换取 → WebSocket 校验。

格式（URL-safe base64 of):
    tenant_id_uuid (16B) || exp_ts (8B big-endian) || nonce (12B) || hmac_sha256_truncated_to_16B

校验规则：签名匹配 + 未过期 + nonce 未重放。
M1 用内存黑名单；多副本部署时改 Redis（v0.x 不必）。
"""

from __future__ import annotations

import base64
import os
import struct
import time
import uuid
from threading import Lock

from .crypto import constant_time_eq, hmac_sha256

_NONCE_LEN = 12
_SIG_LEN = 16


class TicketError(Exception):
    code: str = "AUTH_FAIL"


class TicketExpired(TicketError):
    pass


class TicketInvalid(TicketError):
    pass


class TicketReplayed(TicketError):
    pass


class _SeenNonces:
    """O(N) 内存黑名单 + 时间窗清理；够 M1 用。"""

    def __init__(self, max_size: int = 4096) -> None:
        self._seen: dict[bytes, float] = {}
        self._lock = Lock()
        self._max = max_size

    def remember(self, nonce: bytes, exp_ts: float) -> None:
        with self._lock:
            self._seen[nonce] = exp_ts
            if len(self._seen) > self._max:
                now = time.time()
                self._seen = {k: v for k, v in self._seen.items() if v > now}

    def has(self, nonce: bytes) -> bool:
        with self._lock:
            return nonce in self._seen


_NONCES = _SeenNonces()


def issue(tenant_id: uuid.UUID, ttl_seconds: int, server_secret: bytes) -> str:
    exp_ts = int(time.time()) + ttl_seconds
    nonce = os.urandom(_NONCE_LEN)
    payload = tenant_id.bytes + struct.pack(">Q", exp_ts) + nonce
    sig = hmac_sha256(server_secret, payload)[:_SIG_LEN]
    blob = payload + sig
    return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def verify(token: str, server_secret: bytes) -> uuid.UUID:
    """成功返回 tenant_id；任何失败抛出 TicketError 子类。"""
    try:
        blob = _b64decode(token)
    except Exception as exc:
        raise TicketInvalid("malformed") from exc

    if len(blob) != 16 + 8 + _NONCE_LEN + _SIG_LEN:
        raise TicketInvalid("length")

    tenant_bytes = blob[:16]
    exp_ts = struct.unpack(">Q", blob[16:24])[0]
    nonce = blob[24 : 24 + _NONCE_LEN]
    sig = blob[24 + _NONCE_LEN :]
    payload = blob[: 24 + _NONCE_LEN]

    expected = hmac_sha256(server_secret, payload)[:_SIG_LEN]
    if not constant_time_eq(sig, expected):
        raise TicketInvalid("signature")

    if exp_ts < time.time():
        raise TicketExpired("expired")

    if _NONCES.has(nonce):
        raise TicketReplayed("replay")

    _NONCES.remember(nonce, float(exp_ts))
    return uuid.UUID(bytes=tenant_bytes)
