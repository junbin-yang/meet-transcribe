"""/v1/speakers — 声纹注册 / 列表 / 删除 / 增量样本。

设计要点（v2 §4.6）：
  - 上传 ≥15s 音频 + SNR ≥15dB，否则 422 VALIDATION_FAILED。
  - ECAPA-TDNN 推理在 asyncio.to_thread 里跑，避免阻塞事件循环。
  - 软删除：deleted_at = NOW()；查询统一过滤 deleted_at IS NULL。
  - 任何写操作后都 reset_cache() 让 matcher LRU 失效。
  - SpeakerOut 不返回 embedding（敏感 + 体积大）。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meet_transcribe.api.errors import APIError
from meet_transcribe.auth.api_key import TenantContext, require_tenant
from meet_transcribe.config.loader import load_config
from meet_transcribe.db.models import AuditLog, Speaker
from meet_transcribe.db.session import get_db
from meet_transcribe.observability.logging import get_logger
from meet_transcribe.speakers import matcher
from meet_transcribe.speakers.embedding import (
    AudioDecodeFailed,
    EmbeddingResult,
    QualityRejected,
    compute_embedding,
    decode_audio,
    merge_embedding,
)

router = APIRouter(tags=["speakers"])
log = get_logger(__name__)


class SpeakerOut(BaseModel):
    id: uuid.UUID
    name: str
    sample_count: int
    snr_db_avg: float | None
    consent_at: datetime | None
    consent_source: str | None
    created_at: datetime


def _to_out(row: Speaker) -> SpeakerOut:
    return SpeakerOut(
        id=row.id,
        name=row.name,
        sample_count=row.sample_count,
        snr_db_avg=row.snr_db_avg,
        consent_at=row.consent_at,
        consent_source=row.consent_source,
        created_at=row.created_at,
    )


async def _process_upload(raw: bytes) -> EmbeddingResult:
    """统一在工作线程里 decode + ECAPA 推理，并把内部异常映射到 APIError。"""
    cfg = load_config().speakers

    def _blocking() -> EmbeddingResult:
        audio = decode_audio(raw)
        return compute_embedding(
            audio,
            model_name=cfg.embedding_model,
            min_sample_seconds=cfg.min_sample_seconds,
            min_snr_db=cfg.min_snr_db,
        )

    try:
        return await asyncio.to_thread(_blocking)
    except AudioDecodeFailed as exc:
        raise APIError("AUDIO_FORMAT_INVALID", str(exc)) from exc
    except QualityRejected as exc:
        detail = {"reason": exc.reason, **exc.detail}
        raise APIError("VALIDATION_FAILED", f"sample rejected: {exc.reason}") from None
    except ValueError as exc:
        raise APIError("VALIDATION_FAILED", str(exc)) from exc
    except Exception as exc:
        log.error("speakers.upload.unexpected", error=type(exc).__name__, detail=str(exc))
        raise APIError("INTERNAL", "embedding pipeline failed") from exc


@router.post("", response_model=SpeakerOut)
async def register_speaker(
    req: Request,
    name: Annotated[str, Form(min_length=1, max_length=128)],
    audio_file: Annotated[UploadFile, File()],
    consent_source: Annotated[str | None, Form(max_length=64)] = None,
    tenant: TenantContext = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
) -> SpeakerOut:
    """注册新声纹。要求 multipart/form-data。

    body:
      - name: 显示名
      - audio_file: 音频样本（WAV/FLAC/OGG/MP3/M4A），≥15s，SNR ≥15dB
      - consent_source: 可选，自由文本（如 "in_app_consent_v1"）
    """
    raw = await audio_file.read()
    if not raw:
        raise APIError("VALIDATION_FAILED", "empty audio file")

    result = await _process_upload(raw)
    now = datetime.now(tz=UTC)

    row = Speaker(
        tenant_id=tenant.tenant_id,
        name=name,
        embedding=result.embedding.tolist(),
        sample_count=1,
        snr_db_avg=float(result.snr_db),
        consent_at=now if consent_source else None,
        consent_source=consent_source,
    )
    db.add(row)

    try:
        await db.flush()
    except Exception as exc:
        await db.rollback()
        log.error("speakers.create.flush_failed", error=type(exc).__name__)
        raise APIError("VALIDATION_FAILED", "speaker name conflict or DB error") from exc

    db.add(
        AuditLog(
            tenant_id=tenant.tenant_id,
            actor=tenant.name,
            action="speaker.create",
            resource_type="speaker",
            resource_id=str(row.id),
            detail_json={
                "name": name,
                "duration_s": round(result.duration_s, 2),
                "snr_db": round(result.snr_db, 2),
                "consent_source": consent_source,
            },
            ip=str(req.client.host) if req.client else None,
        )
    )
    await db.commit()
    await db.refresh(row)

    matcher.reset_cache()
    log.info(
        "speakers.create.ok",
        speaker_id=str(row.id),
        tenant_id=str(tenant.tenant_id),
        duration_s=round(result.duration_s, 2),
        snr_db=round(result.snr_db, 2),
    )
    return _to_out(row)


@router.get("", response_model=list[SpeakerOut])
async def list_speakers(
    tenant: TenantContext = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
) -> list[SpeakerOut]:
    """列出当前 tenant 的活跃声纹。"""
    stmt = (
        select(Speaker)
        .where(Speaker.tenant_id == tenant.tenant_id, Speaker.deleted_at.is_(None))
        .order_by(Speaker.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_out(r) for r in rows]


@router.delete("/{speaker_id}", status_code=204, response_class=Response)
async def delete_speaker(
    speaker_id: uuid.UUID,
    req: Request,
    tenant: TenantContext = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """软删除：deleted_at = NOW()。后续匹配查询自动过滤。"""
    stmt = (
        select(Speaker)
        .where(
            Speaker.id == speaker_id,
            Speaker.tenant_id == tenant.tenant_id,
            Speaker.deleted_at.is_(None),
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise APIError("NOT_FOUND", "speaker not found")

    row.deleted_at = datetime.now(tz=UTC)
    db.add(
        AuditLog(
            tenant_id=tenant.tenant_id,
            actor=tenant.name,
            action="speaker.delete",
            resource_type="speaker",
            resource_id=str(row.id),
            detail_json={"name": row.name},
            ip=str(req.client.host) if req.client else None,
        )
    )
    await db.commit()
    matcher.reset_cache()
    log.info("speakers.delete.ok", speaker_id=str(speaker_id), tenant_id=str(tenant.tenant_id))
    return Response(status_code=204)


@router.post("/{speaker_id}/samples", response_model=SpeakerOut)
async def add_sample(
    speaker_id: uuid.UUID,
    req: Request,
    audio_file: Annotated[UploadFile, File()],
    tenant: TenantContext = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
) -> SpeakerOut:
    """追加样本：增量平均 embedding + sample_count += 1 + snr 滑动均值。"""
    raw = await audio_file.read()
    if not raw:
        raise APIError("VALIDATION_FAILED", "empty audio file")

    stmt = (
        select(Speaker)
        .where(
            Speaker.id == speaker_id,
            Speaker.tenant_id == tenant.tenant_id,
            Speaker.deleted_at.is_(None),
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise APIError("NOT_FOUND", "speaker not found")

    result = await _process_upload(raw)

    existing_vec = np.asarray(row.embedding, dtype=np.float32)
    try:
        merged = merge_embedding(existing_vec, row.sample_count, result.embedding)
    except QualityRejected as exc:
        raise APIError("VALIDATION_FAILED", f"merge rejected: {exc.reason}") from None

    old_snr = row.snr_db_avg if row.snr_db_avg is not None else result.snr_db
    new_snr = (old_snr * row.sample_count + result.snr_db) / (row.sample_count + 1)

    row.embedding = merged.tolist()
    row.sample_count = row.sample_count + 1
    row.snr_db_avg = float(new_snr)

    db.add(
        AuditLog(
            tenant_id=tenant.tenant_id,
            actor=tenant.name,
            action="speaker.add_sample",
            resource_type="speaker",
            resource_id=str(row.id),
            detail_json={
                "sample_count": row.sample_count,
                "duration_s": round(result.duration_s, 2),
                "snr_db": round(result.snr_db, 2),
            },
            ip=str(req.client.host) if req.client else None,
        )
    )
    await db.commit()
    await db.refresh(row)

    matcher.reset_cache()
    log.info(
        "speakers.add_sample.ok",
        speaker_id=str(speaker_id),
        tenant_id=str(tenant.tenant_id),
        sample_count=row.sample_count,
    )
    return _to_out(row)
