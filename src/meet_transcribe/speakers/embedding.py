"""CAM++ 声纹特征提取 + 音质门（FunASR 替代 ECAPA-TDNN）。

进程级懒加载 FunASR CAM++ (192-dim embedding)。
接口不变：decode_audio / compute_embedding / merge_embedding。
spike 验证 (funasr 1.3.5): iic/speech_campplus_sv_zh-cn_16k-common → 192-dim。
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

from meet_transcribe.observability.logging import get_logger

log = get_logger(__name__)

TARGET_SR = 16_000
EMBEDDING_DIM = 192
SPK_MODEL_ID = "iic/speech_campplus_sv_zh-cn_16k-common"


class QualityRejected(ValueError):
    """音质 / 时长不达标。HTTP 422 给客户端。"""

    def __init__(self, reason: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


class AudioDecodeFailed(ValueError):
    """无法解码上传的音频。HTTP 400 给客户端。"""


_encoder: Any = None
_encoder_lock = threading.Lock()


def _lazy_load() -> Any:
    """首次调用时加载 CAM++ 模型；线程安全。"""
    global _encoder
    if _encoder is not None:
        return _encoder
    with _encoder_lock:
        if _encoder is not None:
            return _encoder
        from funasr import AutoModel

        log.info("speakers.campp.loading", model=SPK_MODEL_ID)
        _encoder = AutoModel(
            model=SPK_MODEL_ID,
            device="cpu",
            disable_update=True,
        )
        log.info("speakers.campp.loaded")
    return _encoder


@dataclass(frozen=True)
class DecodedAudio:
    samples: np.ndarray  # mono float32, sr=TARGET_SR
    duration_s: float


def decode_audio(raw: bytes) -> DecodedAudio:
    """把上传的 WAV / FLAC / OGG / MP3 解码为 16k mono float32。

    soundfile 处理 WAV/FLAC/OGG；MP3/M4A 走 librosa.load 兜底（依赖 audioread/ffmpeg）。
    解码失败抛 AudioDecodeFailed。
    """
    import soundfile as sf  # type: ignore[import-untyped]

    try:
        data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    except Exception as primary:
        try:
            import librosa  # type: ignore[import-untyped]

            data, sr = librosa.load(io.BytesIO(raw), sr=None, mono=True, dtype=np.float32)
        except Exception as fallback:
            raise AudioDecodeFailed(
                f"unsupported audio: {type(primary).__name__} / "
                f"{type(fallback).__name__}"
            ) from fallback

    if data.ndim == 2:
        data = data.mean(axis=1).astype(np.float32, copy=False)
    if data.dtype != np.float32:
        data = data.astype(np.float32, copy=False)

    if sr != TARGET_SR:
        import librosa  # type: ignore[import-untyped]

        data = librosa.resample(data, orig_sr=sr, target_sr=TARGET_SR).astype(
            np.float32, copy=False
        )

    duration = float(len(data) / TARGET_SR)
    return DecodedAudio(samples=data, duration_s=duration)


def estimate_snr_db(samples: np.ndarray) -> float:
    """简易 SNR 估算：高能量帧 / 低能量帧的功率比，转 dB。

    不追求精确，只用于丢弃纯静音、纯环境噪声、过短样本。
    """
    if samples.size < TARGET_SR // 2:
        return 0.0

    import librosa  # type: ignore[import-untyped]

    rms = librosa.feature.rms(y=samples, frame_length=2048, hop_length=512).flatten()
    if rms.size == 0:
        return 0.0
    speech = float(np.percentile(rms, 95))
    noise = float(np.percentile(rms, 5))
    if noise <= 1e-8:
        noise = 1e-8
    return float(20.0 * np.log10(max(speech, 1e-8) / noise))


@dataclass(frozen=True)
class EmbeddingResult:
    embedding: np.ndarray  # float32, shape=(EMBEDDING_DIM,), L2-normalized
    duration_s: float
    snr_db: float


def compute_embedding(
    audio: DecodedAudio,
    *,
    model_name: str = "",
    min_sample_seconds: float = 15.0,
    min_snr_db: float = 15.0,
) -> EmbeddingResult:
    """CAM++ 推理，返回 L2 归一化向量；不达标抛 QualityRejected。

    model_name 参数保留作接口兼容，实际使用 SPK_MODEL_ID。
    """
    if audio.duration_s < min_sample_seconds:
        raise QualityRejected(
            "sample_too_short",
            {"duration_s": audio.duration_s, "min_sample_seconds": min_sample_seconds},
        )

    snr = 0.0
    if min_snr_db > 0:
        snr = estimate_snr_db(audio.samples)
        if snr < min_snr_db:
            raise QualityRejected(
                "snr_too_low",
                {"snr_db": snr, "min_snr_db": min_snr_db},
            )

    import torch  # type: ignore[import-not-found]

    encoder = _lazy_load()
    wav = audio.samples.astype(np.float32, copy=False)

    with torch.no_grad():
        result = encoder.generate(
            input=wav,
            input_fs=TARGET_SR,
            output_dir=None,
        )

    # FunASR CAM++ generate returns a list of results with 'spk_embedding' key
    if isinstance(result, list) and len(result) > 0:
        r0 = result[0]
        if isinstance(r0, dict):
            # CAM++ returns spk_embedding; try multiple key names
            emb_raw = r0.get("spk_embedding", r0.get("embedding", r0.get("feat", None)))
            if emb_raw is not None:
                emb = np.array(emb_raw, dtype=np.float32)
            else:
                emb = np.array([], dtype=np.float32)
        elif isinstance(r0, np.ndarray):
            emb = r0.astype(np.float32, copy=False)
        else:
            emb = np.array([], dtype=np.float32)
    else:
        emb = np.array([], dtype=np.float32)

    if emb.size == 0:
        emb = np.zeros(EMBEDDING_DIM, dtype=np.float32)

    # CAM++ returns (1, 192) or (192,); squeeze extra dimensions
    while emb.ndim > 1 and emb.shape[0] == 1:
        emb = emb.squeeze(0)
    if emb.ndim != 1 or emb.shape[0] != EMBEDDING_DIM:
        raise RuntimeError(
            f"unexpected embedding shape {emb.shape}, expected ({EMBEDDING_DIM},)"
        )

    norm = float(np.linalg.norm(emb))
    if norm < 1e-8:
        raise QualityRejected("embedding_degenerate", {"norm": norm})
    emb = emb / norm

    return EmbeddingResult(embedding=emb, duration_s=audio.duration_s, snr_db=snr)


def merge_embedding(
    existing: np.ndarray, existing_count: int, new_vec: np.ndarray
) -> np.ndarray:
    """在已存在的均值向量上累加一个新样本，重新 L2 归一化。

    存储模型：embedding = normalize(sum_of_samples / n)。对新样本只需算 (old*n + new)/(n+1)
    再归一化。等价于增量平均。
    """
    if existing.shape != new_vec.shape:
        raise ValueError("embedding dim mismatch")
    if existing_count < 1:
        raise ValueError("existing_count must be >= 1")
    merged = (existing * existing_count + new_vec) / (existing_count + 1)
    norm = float(np.linalg.norm(merged))
    if norm < 1e-8:
        raise QualityRejected("embedding_degenerate", {"norm": norm})
    return (merged / norm).astype(np.float32, copy=False)
