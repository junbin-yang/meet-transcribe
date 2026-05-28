"""speakers.embedding 的纯函数测试：音质门 / SNR / merge。

不加载 ECAPA 模型；只验证 decode_audio + estimate_snr_db + merge_embedding 行为。
"""

from __future__ import annotations

import io

import numpy as np
import pytest

from meet_transcribe.speakers.embedding import (
    EMBEDDING_DIM,
    TARGET_SR,
    DecodedAudio,
    QualityRejected,
    compute_embedding,
    decode_audio,
    estimate_snr_db,
    merge_embedding,
)


def _wav_bytes(samples: np.ndarray, sr: int = TARGET_SR) -> bytes:
    import soundfile as sf  # type: ignore[import-untyped]

    buf = io.BytesIO()
    sf.write(buf, samples.astype(np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def test_decode_wav_roundtrip_resamples_to_16k() -> None:
    rng = np.random.default_rng(42)
    samples = rng.standard_normal(8000 * 3).astype(np.float32) * 0.1
    raw = _wav_bytes(samples, sr=8000)

    decoded = decode_audio(raw)

    assert decoded.samples.dtype == np.float32
    assert abs(decoded.duration_s - 3.0) < 0.05
    assert len(decoded.samples) == int(decoded.duration_s * TARGET_SR)


def test_estimate_snr_db_high_for_speech_like_signal() -> None:
    """脉冲 + 极低噪：能量分布动态高于纯噪声。"""
    rng = np.random.default_rng(7)
    n = TARGET_SR * 2
    noise = rng.standard_normal(n).astype(np.float32) * 1e-4
    speech = np.zeros(n, dtype=np.float32)
    for start in range(0, n, TARGET_SR // 4):
        speech[start : start + TARGET_SR // 8] = 0.3
    signal = speech + noise

    pure_noise_snr = estimate_snr_db(noise)
    signal_snr = estimate_snr_db(signal)
    assert signal_snr > pure_noise_snr
    assert signal_snr > 10.0


def test_estimate_snr_db_returns_zero_for_too_short_input() -> None:
    too_short = np.zeros(TARGET_SR // 4, dtype=np.float32)
    assert estimate_snr_db(too_short) == 0.0


def test_compute_embedding_rejects_short_sample() -> None:
    audio = DecodedAudio(samples=np.zeros(TARGET_SR * 5, dtype=np.float32), duration_s=5.0)
    with pytest.raises(QualityRejected) as exc:
        compute_embedding(
            audio,
            model_name="speechbrain/spkrec-ecapa-voxceleb",
            min_sample_seconds=15.0,
            min_snr_db=15.0,
        )
    assert exc.value.reason == "sample_too_short"


def test_compute_embedding_rejects_low_snr_silence() -> None:
    audio = DecodedAudio(
        samples=np.zeros(TARGET_SR * 20, dtype=np.float32), duration_s=20.0
    )
    with pytest.raises(QualityRejected) as exc:
        compute_embedding(
            audio,
            model_name="speechbrain/spkrec-ecapa-voxceleb",
            min_sample_seconds=15.0,
            min_snr_db=15.0,
        )
    assert exc.value.reason == "snr_too_low"


def test_merge_embedding_increments_and_renormalizes() -> None:
    rng = np.random.default_rng(3)
    a = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    a = a / np.linalg.norm(a)
    b = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    b = b / np.linalg.norm(b)

    merged = merge_embedding(a, existing_count=3, new_vec=b)

    assert merged.shape == (EMBEDDING_DIM,)
    assert merged.dtype == np.float32
    assert abs(np.linalg.norm(merged) - 1.0) < 1e-5


def test_merge_embedding_rejects_degenerate_cancel() -> None:
    a = np.ones(EMBEDDING_DIM, dtype=np.float32)
    a = a / np.linalg.norm(a)
    b = -a * 4  # (a*4 + (-a*4))/5 = 0
    with pytest.raises(QualityRejected) as exc:
        merge_embedding(a, existing_count=4, new_vec=b)
    assert exc.value.reason == "embedding_degenerate"


def test_merge_embedding_validates_inputs() -> None:
    a = np.ones(EMBEDDING_DIM, dtype=np.float32)
    with pytest.raises(ValueError):
        merge_embedding(a[:10], existing_count=1, new_vec=a)
    with pytest.raises(ValueError):
        merge_embedding(a, existing_count=0, new_vec=a)
