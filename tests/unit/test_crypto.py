"""auth.crypto unit tests."""

from __future__ import annotations

import base64
import os

import pytest

from meet_transcribe.auth import crypto


def test_hmac_sha256_deterministic() -> None:
    secret = b"server-secret"
    data = b"api-key-plaintext"
    a = crypto.hmac_sha256(secret, data)
    b = crypto.hmac_sha256(secret, data)
    assert a == b
    assert len(a) == 32


def test_hmac_sha256_changes_with_key_or_data() -> None:
    a = crypto.hmac_sha256(b"k1", b"d")
    assert crypto.hmac_sha256(b"k2", b"d") != a
    assert crypto.hmac_sha256(b"k1", b"d2") != a


def test_hash_api_key_uses_server_secret() -> None:
    secret = b"\x00" * 32
    h1 = crypto.hash_api_key("mt_aaa", secret)
    h2 = crypto.hash_api_key("mt_aaa", secret)
    h3 = crypto.hash_api_key("mt_bbb", secret)
    assert h1 == h2
    assert h1 != h3


def test_constant_time_eq() -> None:
    assert crypto.constant_time_eq(b"abc", b"abc")
    assert not crypto.constant_time_eq(b"abc", b"abd")
    assert not crypto.constant_time_eq(b"abc", b"abcd")


def test_load_kms_key_validates_length() -> None:
    good = base64.b64encode(b"\x01" * 32).decode()
    assert crypto.load_kms_key(good) == b"\x01" * 32
    with pytest.raises(ValueError):
        crypto.load_kms_key(base64.b64encode(b"short").decode())


def test_aes_gcm_roundtrip() -> None:
    key = os.urandom(32)
    sealed = crypto.aes_gcm_encrypt("会议纪要：内部测试", key)
    pt = crypto.aes_gcm_decrypt(sealed.ciphertext, sealed.iv, sealed.tag, key)
    assert pt == "会议纪要：内部测试"


def test_aes_gcm_iv_is_unique_per_call() -> None:
    key = os.urandom(32)
    a = crypto.aes_gcm_encrypt("hello", key)
    b = crypto.aes_gcm_encrypt("hello", key)
    assert a.iv != b.iv
    assert a.ciphertext != b.ciphertext


def test_aes_gcm_tampered_ciphertext_rejected() -> None:
    key = os.urandom(32)
    sealed = crypto.aes_gcm_encrypt("secret", key)
    bad = bytearray(sealed.ciphertext)
    bad[0] ^= 0xFF
    with pytest.raises(Exception):
        crypto.aes_gcm_decrypt(bytes(bad), sealed.iv, sealed.tag, key)


def test_random_token_url_safe() -> None:
    t = crypto.random_token(24)
    assert all(c.isalnum() or c in "-_" for c in t)
    assert len(t) >= 32


def test_aes_gcm_rejects_wrong_key_length() -> None:
    with pytest.raises(ValueError):
        crypto.aes_gcm_encrypt("x", b"\x00" * 16)
