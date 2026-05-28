"""管理员路由：创建 tenant + 签发 API key。

鉴权：要求 X-Admin-Token header == MT_ADMIN_TOKEN env。
适合运维人员/部署脚本初始化用；不暴露给租户。
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from meet_transcribe.api.errors import APIError
from meet_transcribe.auth.crypto import hash_api_key, random_token
from meet_transcribe.config.loader import load_config
from meet_transcribe.db.models import ApiKey, Tenant
from meet_transcribe.db.session import get_db

router = APIRouter(tags=["admin"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    cfg = load_config()
    expected = cfg.secrets.admin_token.get_secret_value() or os.environ.get(
        "MT_ADMIN_TOKEN", ""
    )
    if not expected:
        raise APIError("INTERNAL", "admin token not configured")
    if not x_admin_token or x_admin_token != expected:
        raise APIError("AUTH_FAIL", "invalid admin token")


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    quota_concurrent: int = Field(default=1, ge=1, le=64)
    quota_minutes_per_day: int = Field(default=60, ge=1, le=24 * 60 * 7)
class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    quota_concurrent: int
    quota_minutes_per_day: int
    created_at: datetime


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    label: str | None
    api_key: str
    created_at: datetime


@router.post("/tenants", response_model=TenantOut, dependencies=[Depends(require_admin)])
async def create_tenant(body: TenantCreate, db: AsyncSession = Depends(get_db)) -> TenantOut:
    t = Tenant(
        name=body.name,
        quota_concurrent=body.quota_concurrent,
        quota_minutes_per_day=body.quota_minutes_per_day,
    )
    db.add(t)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise APIError("VALIDATION_FAILED", "tenant name conflict") from exc
    await db.refresh(t)
    return TenantOut(
        id=t.id,
        name=t.name,
        quota_concurrent=t.quota_concurrent,
        quota_minutes_per_day=t.quota_minutes_per_day,
        created_at=t.created_at,
    )


@router.post(
    "/tenants/{tenant_id}/api-keys",
    response_model=ApiKeyOut,
    dependencies=[Depends(require_admin)],
)
async def create_api_key(
    tenant_id: uuid.UUID,
    label: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiKeyOut:
    cfg = load_config()
    server_secret = cfg.secrets.server_secret.get_secret_value().encode("utf-8")
    if not server_secret:
        raise APIError("INTERNAL", "server secret missing")

    plaintext = "mt_" + random_token(24)
    key_hash = hash_api_key(plaintext, server_secret)

    row = ApiKey(tenant_id=tenant_id, key_hash=key_hash, label=label)
    db.add(row)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise APIError("VALIDATION_FAILED", "tenant not found") from exc
    await db.refresh(row)

    return ApiKeyOut(
        id=row.id,
        tenant_id=row.tenant_id,
        label=row.label,
        api_key=plaintext,
        created_at=row.created_at or datetime.now(tz=UTC),
    )
