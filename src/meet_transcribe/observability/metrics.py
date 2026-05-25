"""Prometheus 指标。v2 设计第 12.4 节锁定的 schema。"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY = CollectorRegistry()

ACTIVE_SESSIONS = Gauge(
    "meet_transcribe_active_sessions",
    "currently open WebSocket sessions",
    labelnames=("tenant_id",),
    registry=REGISTRY,
)

SESSIONS_TOTAL = Counter(
    "meet_transcribe_sessions_total",
    "lifetime number of accepted WS sessions",
    labelnames=("tenant_id", "outcome"),
    registry=REGISTRY,
)

AUDIO_INPUT_SECONDS = Counter(
    "meet_transcribe_audio_input_seconds_total",
    "total seconds of audio fed into the engine",
    labelnames=("tenant_id",),
    registry=REGISTRY,
)

INFERENCE_LATENCY = Histogram(
    "meet_transcribe_inference_latency_seconds",
    "wall-clock latency of a single chunk inference",
    labelnames=("kind",),
    buckets=(0.05, 0.1, 0.25, 0.5, 0.8, 1.5, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

WORDS_EMITTED = Counter(
    "meet_transcribe_words_emitted_total",
    "words emitted to clients (final only)",
    labelnames=("tenant_id",),
    registry=REGISTRY,
)

ERRORS_TOTAL = Counter(
    "meet_transcribe_errors_total",
    "errors emitted to clients",
    labelnames=("code",),
    registry=REGISTRY,
)

GPU_MEMORY_USED_BYTES = Gauge(
    "meet_transcribe_gpu_memory_used_bytes",
    "torch.cuda.memory_allocated()",
    registry=REGISTRY,
)

AUTH_FAILURES = Counter(
    "meet_transcribe_auth_failures_total",
    "failed API key / ticket validations",
    labelnames=("reason",),
    registry=REGISTRY,
)


def render_latest() -> tuple[bytes, str]:
    """供 /metrics 端点直接返回。"""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
