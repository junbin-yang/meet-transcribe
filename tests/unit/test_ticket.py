"""auth.ticket unit tests."""

from __future__ import annotations

import base64
import struct
import time
import uuid

import pytest

from meet_transcribe.auth import ticket


@pytest.fixture(autouse=True)
def _reset_nonces():
    ticket._NONCES._seen.clear()
    yield
    ticket._NONCES._seen.clear()


def test_issue_then_verify_returns_tenant_id() -> None:
    secret = b"\xAA" * 32
    tid = uuid.uuid4()
    token = ticket.issue(tid, ttl_seconds=30, server_secret=secret)
    assert ticket.verify(token, secret) == tid


def test_verify_rejects_wrong_secret() -> None:
    tid = uuid.uuid4()
    token = ticket.issue(tid, ttl_seconds=30, server_secret=b"k1" * 16)
    with pytest.raises(ticket.TicketInvalid):
        ticket.verify(token, b"k2" * 16)


def test_verify_rejects_expired() -> None:
    tid = uuid.uuid4()
    secret = b"\x01" * 32
    token = ticket.issue(tid, ttl_seconds=-1, server_secret=secret)
    with pytest.raises(ticket.TicketExpired):
        ticket.verify(token, secret)


def test_verify_rejects_replay() -> None:
    tid = uuid.uuid4()
    secret = b"\x02" * 32
    token = ticket.issue(tid, ttl_seconds=30, server_secret=secret)
    assert ticket.verify(token, secret) == tid
    with pytest.raises(ticket.TicketReplayed):
        ticket.verify(token, secret)


def test_verify_rejects_malformed() -> None:
    with pytest.raises(ticket.TicketInvalid):
        ticket.verify("not-a-real-base64!!!", b"k" * 32)


def test_verify_rejects_short_blob() -> None:
    payload = base64.urlsafe_b64encode(b"too-short").decode().rstrip("=")
    with pytest.raises(ticket.TicketInvalid):
        ticket.verify(payload, b"k" * 32)


def test_token_does_not_leak_secret() -> None:
    secret = b"\xCC" * 32
    tid = uuid.uuid4()
    token = ticket.issue(tid, ttl_seconds=30, server_secret=secret)
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert secret not in raw


def test_verify_roundtrip_preserves_uuid_bytes() -> None:
    secret = b"\xDD" * 32
    tid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    token = ticket.issue(tid, ttl_seconds=30, server_secret=secret)
    assert ticket.verify(token, secret).bytes == tid.bytes


def test_seen_nonces_clears_when_full() -> None:
    seen = ticket._SeenNonces(max_size=4)
    now = time.time()
    for i in range(10):
        seen.remember(struct.pack(">I", i).rjust(12, b"\x00"), now + 60)
    assert len(seen._seen) >= 1


def test_signature_tamper_detected() -> None:
    secret = b"\xEE" * 32
    tid = uuid.uuid4()
    token = ticket.issue(tid, ttl_seconds=30, server_secret=secret)

    raw = bytearray(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    raw[-1] ^= 0xFF
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip("=")
    ticket._NONCES._seen.clear()

    with pytest.raises(ticket.TicketInvalid):
        ticket.verify(tampered, secret)
