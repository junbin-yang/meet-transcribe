"""WhisperLiveKit 上游契约测试。

每次升级 vendored/whisperlivekit 后运行：
- 校验关键模块、类、函数仍存在
- 校验 TranscriptionEngine / AudioProcessor 的关键参数仍被接受

CI 把这一组当作 upstream 升级的 gate；任何一个失败都意味着 adapter 需要修。
"""

from __future__ import annotations

import inspect

import pytest

from meet_transcribe.core import whisperlivekit_adapter as adapter  # noqa: F401


def test_upstream_modules_importable() -> None:
    from whisperlivekit import audio_processor, core  # type: ignore[import-not-found]

    assert hasattr(core, "TranscriptionEngine"), "TranscriptionEngine missing in upstream"
    assert hasattr(audio_processor, "AudioProcessor"), "AudioProcessor missing in upstream"


def test_transcription_engine_is_singleton() -> None:
    from whisperlivekit.core import TranscriptionEngine  # type: ignore[import-not-found]

    assert hasattr(TranscriptionEngine, "_instance")
    assert hasattr(TranscriptionEngine, "_lock")
    assert hasattr(TranscriptionEngine, "reset"), "missing reset() — needed for tests"


@pytest.mark.parametrize(
    "param",
    [
        "model",
        "lan",
        "backend",
        "compute_type",
        "device",
        "diarization",
        "no_vad",
        "pcm_input",
    ],
)
def test_engine_init_accepts_kwargs(param: str) -> None:
    """EngineSpec 映射到 upstream 的关键 kwargs 必须仍被接受。"""
    from whisperlivekit.core import TranscriptionEngine  # type: ignore[import-not-found]

    sig = inspect.signature(TranscriptionEngine._do_init)
    params = sig.parameters
    has_var_keyword = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    assert has_var_keyword or param in params, (
        f"upstream TranscriptionEngine._do_init no longer accepts {param!r}; "
        "update meet_transcribe.core.whisperlivekit_adapter.init_engine"
    )


def test_audio_processor_accepts_engine_handoff() -> None:
    from whisperlivekit.audio_processor import AudioProcessor  # type: ignore[import-not-found]

    sig = inspect.signature(AudioProcessor.__init__)
    has_var_keyword = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    assert has_var_keyword, (
        "upstream AudioProcessor.__init__ no longer accepts **kwargs; "
        "transcription_engine handoff broken"
    )


def test_adapter_engine_spec_defaults_match_v2_design() -> None:
    """v2 设计第 3.3 节锁定的默认值不应被静默改动。"""
    spec = adapter.EngineSpec()
    assert spec.model == "medium"
    assert spec.language == "zh"
    assert spec.backend == "faster-whisper"
    assert spec.compute_type == "float16"
    assert spec.device == "cuda"
    assert spec.vad is True
    assert spec.pcm_input is True
    assert spec.diarization is False


def test_upstream_commit_resolves() -> None:
    sha = adapter.upstream_commit()
    assert sha == "unknown" or len(sha) >= 7
