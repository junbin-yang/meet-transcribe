"""FunASR 适配层 — 单引擎架构（组件模型统一 ASR + SPK）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meet_transcribe.observability.logging import get_logger

log = get_logger(__name__)

# SPK 引擎组件模型
SPK_VAD_MODEL = "fsmn-vad"
SPK_PUNC_MODEL = "ct-punc"
SPK_SPK_MODEL = "cam++"

# 模型档位预设
MODEL_TIERS: dict[str, str] = {
    "large": "paraformer-zh",
    "paraformer-zh": "paraformer-zh",
}


def _resolve_asr_model(name: str) -> str:
    """将模型档位名或 ModelScope ID 解析为 FunASR 模型名。"""
    if name in MODEL_TIERS:
        return MODEL_TIERS[name]
    return name


@dataclass(frozen=True)
class FunASRSpec:
    """FunASR 引擎配置。"""

    model: str = "large"  # "large" | "paraformer-zh" | ModelScope ID
    device: str = "cuda"
    language: str = "zh"
    sample_rate: int = 16000
    batch_size_s: int = 500


_spk_engine: Any = None
_spk_engine_asr_model: str | None = None


def init_spk_engine(device: str = "cuda", asr_model: str = "large") -> Any:
    """初始化 SPK diarization 引擎（组件模型，按需调用）。

    asr_model: 模型档位名 ("large") 或 ModelScope ID。
    """
    global _spk_engine, _spk_engine_asr_model

    resolved = _resolve_asr_model(asr_model)

    if _spk_engine is not None and _spk_engine_asr_model == resolved:
        return _spk_engine

    from funasr import AutoModel

    _spk_engine = AutoModel(
        model=resolved,
        vad_model=SPK_VAD_MODEL,
        punc_model=SPK_PUNC_MODEL,
        spk_model=SPK_SPK_MODEL,
        device=device,
        disable_update=True,
    )
    _spk_engine_asr_model = resolved
    log.info("funasr.spk_engine.init", model=resolved, device=device)
    return _spk_engine


def reset_engine() -> None:
    """测试用 — 重置全局 engine 状态。"""
    global _spk_engine, _spk_engine_asr_model
    _spk_engine = None
    _spk_engine_asr_model = None
