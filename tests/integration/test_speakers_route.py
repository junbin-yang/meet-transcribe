"""/v1/speakers 路由的轻量集成测试。

仅测试鉴权失败 / openapi 挂载这类无需真实 ECAPA / DB 的轻量行为；
完整 E2E（端到端样本注册 → 匹配准确率）走 scripts/m2a_verify.py，
需要外部数据集（AISHELL-4 切片）+ 实际 PG 实例。

Fact-Forcing Gate facts:
  1. Callers: pytest 自动发现；手动 `pytest tests/integration/test_speakers_route.py --no-cov`
  2. No duplicate: Glob tests/integration/test_speakers*.py → 无现存文件
  3. Data fields: 用 fastapi.testclient.TestClient 发 multipart，不接触磁盘 / DB
  4. User verbatim: 用户原话 "开始" — 推进 M2a #19 验收子任务
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    """用 `with` 触发 lifespan，懒初始化 engine（不需要真实 DB 可达，create_async_engine 是惰性的）。"""
    from meet_transcribe.api.app import app

    with TestClient(app) as c:
        yield c


def test_post_speakers_requires_auth(client: TestClient) -> None:
    """没带 Bearer token 应返回 401 / AUTH_FAIL。"""
    resp = client.post(
        "/v1/speakers",
        data={"name": "alice"},
        files={"audio_file": ("a.wav", b"\x00\x00", "audio/wav")},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_FAIL"


def test_get_speakers_requires_auth(client: TestClient) -> None:
    resp = client.get("/v1/speakers")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_FAIL"


def test_delete_speaker_requires_auth(client: TestClient) -> None:
    resp = client.delete("/v1/speakers/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_FAIL"


def test_post_sample_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/v1/speakers/00000000-0000-0000-0000-000000000000/samples",
        files={"audio_file": ("a.wav", b"\x00\x00", "audio/wav")},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "AUTH_FAIL"


def test_speakers_routes_mounted_in_openapi(client: TestClient) -> None:
    """openapi.json 必须包含 4 个声纹路由，否则 router 没挂上。"""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]

    assert "/v1/speakers" in paths
    assert "post" in paths["/v1/speakers"]
    assert "get" in paths["/v1/speakers"]

    assert "/v1/speakers/{speaker_id}" in paths
    assert "delete" in paths["/v1/speakers/{speaker_id}"]

    assert "/v1/speakers/{speaker_id}/samples" in paths
    assert "post" in paths["/v1/speakers/{speaker_id}/samples"]
