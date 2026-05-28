"""配置加载：YAML + 环境变量覆盖。

优先级：env (MT_ 前缀) > configs/meet-transcribe.yaml > configs/meet-transcribe.example.yaml
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    workers: int = Field(default=1, ge=1, le=1)
    cors_origins: list[str] = []
    ticket_ttl_seconds: int = Field(default=30, ge=5, le=300)


class DatabaseConfig(BaseModel):
    url: str
    pool_size: int = 5
    echo: bool = False


class ASRConfig(BaseModel):
    model: str = "medium"
    device: Literal["cuda", "cpu"] = "cuda"
    compute_type: Literal["float16", "int8_float16", "int8", "float32"] = "float16"
    language: str = "zh"
    beam_size: int = Field(default=5, ge=1, le=10)
    itn_enabled: bool = False
    text_postprocess: Literal["none", "t2s", "s2t"] = "t2s"


class StreamingConfig(BaseModel):
    min_chunk_size_seconds: float = 0.5
    vad_enabled: bool = True
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class DiarizationConfig(BaseModel):
    enabled: bool = False
    backend: Literal["sortformer", "diart", "passthrough"] = "sortformer"
    num_speakers_max: int = Field(default=6, ge=1, le=20)


class SpeakersConfig(BaseModel):
    min_sample_seconds: float = 15.0
    min_snr_db: float = 15.0
    embedding_model: str = "speechbrain/spkrec-ecapa-voxceleb"
    match_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    cache_size: int = 1024


class HotwordsConfig(BaseModel):
    enabled: bool = True
    max_words_per_session: int = 50
    max_word_length: int = 20


class SecurityConfig(BaseModel):
    hash_algorithm: Literal["hmac_sha256"] = "hmac_sha256"
    transcript_encryption_enabled: bool = True


class RetentionConfig(BaseModel):
    default_days: int = Field(default=90, ge=1, le=3650)
    audit_log_min_days: int = Field(default=180, ge=180, le=3650)


class ObservabilityConfig(BaseModel):
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "json"
    metrics_enabled: bool = True
    metrics_path: str = "/metrics"


class GPUConfig(BaseModel):
    cuda_alloc_conf: str = "expandable_segments:True"
    oom_restart: bool = True
    high_watermark_mb: int = 6500


class _Secrets(BaseSettings):
    """从环境变量读取的敏感配置。永远不写 yaml。"""

    model_config = SettingsConfigDict(
        env_prefix="MT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    db_password: SecretStr = SecretStr("")
    server_secret: SecretStr = SecretStr("")
    kms_key: SecretStr = SecretStr("")
    admin_token: SecretStr = SecretStr("")


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig
    asr: ASRConfig = ASRConfig()
    streaming: StreamingConfig = StreamingConfig()
    diarization: DiarizationConfig = DiarizationConfig()
    speakers: SpeakersConfig = SpeakersConfig()
    hotwords: HotwordsConfig = HotwordsConfig()
    security: SecurityConfig = SecurityConfig()
    retention: RetentionConfig = RetentionConfig()
    observability: ObservabilityConfig = ObservabilityConfig()
    gpu: GPUConfig = GPUConfig()
    secrets: _Secrets = Field(default_factory=_Secrets)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _config_search_paths() -> list[Path]:
    explicit = os.environ.get("MT_CONFIG_FILE")
    if explicit:
        return [Path(explicit)]
    here = Path(__file__).resolve().parents[3]
    return [
        here / "configs" / "meet-transcribe.yaml",
        here / "configs" / "meet-transcribe.example.yaml",
    ]


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    """读取 yaml + env，构造 AppConfig 单例。"""
    merged: dict[str, Any] = {}
    for path in _config_search_paths():
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            merged = _deep_merge(merged, data)
            break

    if not merged:
        raise RuntimeError(
            "no config found; expected configs/meet-transcribe.yaml or .example.yaml"
        )

    return AppConfig(**merged)
