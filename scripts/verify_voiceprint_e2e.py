"""声纹识别 端到端识别验证脚本。

场景：tenant 库里已经用 cc_ys1.wav 注册了一个名为 Tom 的声纹。
本脚本用 cc_ys1_tts.wav（同一人的另一段语音）模拟实时流场景：

  1. 直接 matcher 路径：本地 ECAPA -> pgvector top-1，断言命中 Tom 且 score ≥ threshold。
  2. resolver 路径：复用 SpeakerResolver.feed_audio / note_segment / trigger，
     模拟 orchestrator 在 diarization=True 下的真实调用链；断言 resolved=tom。

不经 WebSocket，不依赖 Whisper 引擎，只验证“人声识别”这一条链路。

用法：
    .venv/Scripts/python.exe scripts/verify_voiceprint_e2e.py \\
        --api-key $MT_TEST_API_KEY \\
        --query-wav tests/assets/cc_ys1_tts.wav \\
        --expect-name Tom \\
        --threshold 0.65

退出码：
    0 = 两条路径都识别成功
    2 = 识别失败（未命中 / score 不达标）
    3 = 前置条件不满足（tenant 找不到 / Tom 未注册 / 音频解码失败）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meet_transcribe.auth.crypto import hash_api_key
from meet_transcribe.config.loader import load_config
from meet_transcribe.db.models import ApiKey, Speaker, Tenant
from meet_transcribe.db.session import (
    dispose,
    get_session_factory,
    init_engine_from_config,
)
from meet_transcribe.speakers.embedding import (
    EMBEDDING_DIM,
    DecodedAudio,
    QualityRejected,
    compute_embedding,
    decode_audio,
)
from meet_transcribe.speakers.matcher import match
from meet_transcribe.speakers.resolver import SpeakerResolver


async def _resolve_tenant(db: AsyncSession, api_key: str, server_secret: bytes) -> uuid.UUID:
    key_hash = hash_api_key(api_key, server_secret)
    stmt = (
        select(Tenant.id)
        .join(ApiKey, ApiKey.tenant_id == Tenant.id)
        .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        .where(Tenant.deleted_at.is_(None))
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        print("[fail] api key 无法解析到 tenant（已撤销或不存在）", file=sys.stderr)
        sys.exit(3)
    return row[0]


async def _find_speaker_by_name(
    db: AsyncSession, *, tenant_id: uuid.UUID, name: str
) -> Speaker | None:
    stmt = (
        select(Speaker)
        .where(
            Speaker.tenant_id == tenant_id,
            Speaker.deleted_at.is_(None),
        )
    )
    rows = (await db.execute(stmt)).scalars().all()
    target = name.strip().lower()
    for row in rows:
        if row.name.strip().lower() == target:
            return row
    return None


def _load_query_audio(path: Path, min_seconds: float) -> DecodedAudio:
    raw = path.read_bytes()
    audio = decode_audio(raw)
    if audio.duration_s < min_seconds:
        print(
            f"[fail] query wav 时长 {audio.duration_s:.2f}s < 最小 {min_seconds:.2f}s",
            file=sys.stderr,
        )
        sys.exit(3)
    return audio


async def _run_direct_match(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    audio: DecodedAudio,
    model_name: str,
    threshold: float,
) -> dict[str, Any]:
    emb = await asyncio.to_thread(
        compute_embedding,
        audio,
        model_name=model_name,
        min_sample_seconds=3.0,
        min_snr_db=0.0,
    )
    if emb.embedding.shape != (EMBEDDING_DIM,):
        return {"matched": False, "reason": "bad_embedding_shape"}
    result = await match(
        db, tenant_id=tenant_id, query=emb.embedding, threshold=threshold
    )
    return {
        "matched": result.matched,
        "id": str(result.speaker_id) if result.speaker_id else None,
        "name": result.name,
        "score": None if result.score == float("-inf") else round(float(result.score), 4),
        "threshold": result.threshold,
    }


async def _run_resolver_path(
    *,
    tenant_id: uuid.UUID,
    audio: DecodedAudio,
    model_name: str,
    threshold: float,
) -> dict[str, Any]:
    factory = get_session_factory()
    resolver = SpeakerResolver(
        tenant_id=tenant_id,
        threshold=threshold,
        embedding_model=model_name,
        session_factory=factory,
    )

    pcm_int16 = (np.clip(audio.samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    resolver.feed_audio(pcm_int16)

    should_trigger = resolver.note_segment(1, 0.0, audio.duration_s)
    if not should_trigger:
        return {
            "matched": False,
            "reason": "note_segment_no_trigger",
            "duration_s": audio.duration_s,
        }

    resolved = await resolver.trigger(1)
    if resolved is None:
        return {"matched": False, "reason": "trigger_no_match"}
    return {"matched": True, **resolved}


async def _main_async(args: argparse.Namespace) -> int:
    cfg = load_config()
    server_secret = cfg.secrets.server_secret.get_secret_value().encode("utf-8")

    init_engine_from_config(cfg)
    factory = get_session_factory()

    try:
        async with factory() as db:
            if args.tenant_id:
                tenant_id = uuid.UUID(args.tenant_id)
            else:
                if not server_secret:
                    print("[fail] MT_SERVER_SECRET 未配置（--api-key 模式必需）", file=sys.stderr)
                    return 3
                tenant_id = await _resolve_tenant(db, args.api_key, server_secret)
            target = await _find_speaker_by_name(
                db, tenant_id=tenant_id, name=args.expect_name
            )
            if target is None:
                print(
                    f"[fail] tenant={tenant_id} 下找不到 name={args.expect_name!r}"
                    f" 的活跃 speaker；请先用 m2a_verify.py 或 web demo 注册",
                    file=sys.stderr,
                )
                return 3
            target_id = str(target.id)

        audio = _load_query_audio(Path(args.query_wav), min_seconds=3.0)

        async with factory() as db:
            direct = await _run_direct_match(
                db,
                tenant_id=tenant_id,
                audio=audio,
                model_name=cfg.speakers.embedding_model,
                threshold=args.threshold,
            )

        resolver_result = await _run_resolver_path(
            tenant_id=tenant_id,
            audio=audio,
            model_name=cfg.speakers.embedding_model,
            threshold=args.threshold,
        )
    finally:
        await dispose()

    direct_pass = (
        direct.get("matched")
        and direct.get("id") == target_id
        and (direct.get("score") or 0.0) >= args.threshold
    )
    resolver_pass = (
        resolver_result.get("matched")
        and resolver_result.get("id") == target_id
        and (resolver_result.get("score") or 0.0) >= args.threshold
    )

    report = {
        "tenant_id": str(tenant_id),
        "expect_name": args.expect_name,
        "expect_id": target_id,
        "threshold": args.threshold,
        "direct": direct,
        "resolver": resolver_result,
        "direct_passed": bool(direct_pass),
        "resolver_passed": bool(resolver_pass),
        "passed": bool(direct_pass and resolver_pass),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not report["passed"]:
        print(
            f"[fail] direct_passed={direct_pass} resolver_passed={resolver_pass}",
            file=sys.stderr,
        )
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="M2b 人声识别端到端验证")
    parser.add_argument(
        "--api-key",
        default=None,
        help="目标 tenant 的 API key（用来反查 tenant_id）；与 --tenant-id 二选一",
    )
    parser.add_argument(
        "--tenant-id",
        default=None,
        help="跳过 API key 反查，直接指定 tenant UUID（dev 验证场景）",
    )
    parser.add_argument(
        "--query-wav",
        default="tests/assets/cc_ys1_tts.wav",
        help="查询音频（默认 cc_ys1_tts.wav）",
    )
    parser.add_argument(
        "--expect-name",
        default="Tom",
        help="期望识别到的 speaker 名（与注册时一致，忽略大小写）",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.65,
        help="余弦相似度阈值（默认 0.65，跟运行时配置保持一致）",
    )
    args = parser.parse_args()

    if not args.api_key and not args.tenant_id:
        parser.error("必须提供 --api-key 或 --tenant-id")

    if sys.platform == "win32":
        # psycopg async 在 Windows 默认 ProactorEventLoop 下不可用。
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        return asyncio.run(_main_async(args))
    except QualityRejected as e:
        print(f"[fail] 音频质量不达标：{e.reason} {e.detail}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
