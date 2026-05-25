"""加密原语。

提供：
  - hmac_sha256 — API key hash 与 ticket 签名
  - aes_gcm_encrypt / aes_gcm_decrypt — transcripts.text 静态加密
  - constant_time_eq — 安全比较

主密钥从 env MT_KMS_KEY 读取，base64(32B)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def hmac_sha256(secret: bytes, data: bytes) -> bytes:
    return hmac.new(secret, data, hashlib.sha256).digest()


def constant_time_eq(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)


def hash_api_key(plaintext: str, server_secret: bytes) -> bytes:
    """对外永远存储这个 hash；明文 API key 只在签发时短暂存在。"""
    return hmac_sha256(server_secret, plaintext.encode("utf-8"))


def load_kms_key(b64: str) -> bytes:
    raw = base64.b64decode(b64)
    if len(raw) != 32:
        raise ValueError(f"MT_KMS_KEY must be 32 bytes after base64, got {len(raw)}")
    return raw


@dataclass(frozen=True)
class Sealed:
    ciphertext: bytes  # 含 GCM tag 的密文
    iv: bytes
    tag: bytes

    def to_db(self) -> tuple[bytes, bytes, bytes]:
        """拆分到 transcripts.text_encrypted / text_iv / text_tag。"""
        return self.ciphertext[:-16], self.iv, self.ciphertext[-16:]


def aes_gcm_encrypt(plaintext: str, key: bytes, *, aad: bytes | None = None) -> Sealed:
    if len(key) != 32:
        raise ValueError("AES-GCM key must be 32 bytes")
    iv = os.urandom(12)
    aead = AESGCM(key)
    ct = aead.encrypt(iv, plaintext.encode("utf-8"), aad)
    return Sealed(ciphertext=ct, iv=iv, tag=ct[-16:])


def aes_gcm_decrypt(
    ciphertext: bytes, iv: bytes, tag: bytes, key: bytes, *, aad: bytes | None = None
) -> str:
    aead = AESGCM(key)
    full = ciphertext + tag if not ciphertext.endswith(tag) else ciphertext
    pt = aead.decrypt(iv, full, aad)
    return pt.decode("utf-8")


def random_token(byte_len: int = 24) -> str:
    return secrets.token_urlsafe(byte_len)
