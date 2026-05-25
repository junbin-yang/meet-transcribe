"""WhisperLiveKit 适配层。

把上游的 TranscriptionEngine + AudioProcessor 包装成项目内的稳定接口，
隔离 upstream 重命名/签名变化对业务代码的冲击。

关键约定（参见 vendored/whisperlivekit/CLAUDE.md）：
  - TranscriptionEngine 是进程级 singleton，不要创建第二个实例。
  - 每个 WebSocket 会话独立创建 AudioProcessor，并把同一个 engine 传入。
  - 跨 session 的语言/热词差异通过 per-session 参数注入，不修改 engine 内部状态。

upstream pin: vendored/whisperlivekit @ 71fe418
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


def _ensure_vendored_on_path() -> None:
    """让 vendored/whisperlivekit 可被 import。"""
    here = Path(__file__).resolve().parents[3]
    vendored = here / "vendored" / "whisperlivekit"
    if vendored.exists() and str(vendored) not in sys.path:
        sys.path.insert(0, str(vendored))


_ensure_vendored_on_path()


@dataclass(frozen=True)
class EngineSpec:
    """初始化 TranscriptionEngine 的最小入参集合。

    upstream 的 WhisperLiveKitConfig 字段很多；本项目只暴露已验证的几项，
    并在适配层内显式映射。其他字段保持 upstream 默认。
    """

    model: str = "medium"
    language: str = "zh"
    backend: str = "faster-whisper"
    compute_type: str = "float16"
    device: str = "cuda"
    diarization: bool = False
    vad: bool = True
    pcm_input: bool = True


class _SupportsAudioProcessor(Protocol):
    """upstream AudioProcessor 暴露给本项目的最小接口（仅用于类型）。"""

    async def process_audio(self, audio: bytes) -> Any: ...
    async def stop(self) -> Any: ...


def init_engine(spec: EngineSpec) -> Any:
    """初始化 / 取得进程级 TranscriptionEngine 单例。

    线程安全由 upstream 保证（core.py: TranscriptionEngine._lock）。
    返回 engine 引用，调用方仅用作传给 AudioProcessor，不应读写其内部状态。
    """
    from whisperlivekit.core import TranscriptionEngine  # type: ignore[import-not-found]

    engine = TranscriptionEngine(
        model=spec.model,
        lan=spec.language,
        backend=spec.backend,
        compute_type=spec.compute_type,
        device=spec.device,
        diarization=spec.diarization,
        no_vad=not spec.vad,
        pcm_input=spec.pcm_input,
    )
    return engine


def make_session_processor(engine: Any, *, language: str | None = None) -> _SupportsAudioProcessor:
    """为一个 WebSocket 会话创建独立 AudioProcessor。

    每个会话必须单独调用一次本函数：上游内部维护
    LocalAgreement 的稳定/未稳定队列、滑窗缓冲、diarization speaker_id 计数器，
    跨 session 共享会导致内容污染（v2 设计 C1 风险）。
    """
    from whisperlivekit.audio_processor import AudioProcessor  # type: ignore[import-not-found]

    kwargs: dict[str, Any] = {"transcription_engine": engine}
    if language is not None:
        kwargs["language"] = language
    return AudioProcessor(**kwargs)


def upstream_commit() -> str:
    """读取 vendored/whisperlivekit 的 HEAD sha；用于 /metrics 与 /ready。"""
    here = Path(__file__).resolve().parents[3]
    head_file = here / ".git" / "modules" / "vendored" / "whisperlivekit" / "HEAD"
    if not head_file.exists():
        head_file = here / "vendored" / "whisperlivekit" / ".git" / "HEAD"
    if not head_file.exists():
        return "unknown"

    head = head_file.read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        ref = head.split(" ", 1)[1].strip()
        ref_path = head_file.parent / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()[:12]
        return "unknown"
    return head[:12]
