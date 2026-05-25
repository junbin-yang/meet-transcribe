"""API Key 鉴权依赖。

客户端发:    Authorization: Bearer <api_key>
服务端:      HMAC-SHA256(server_secret, api_key) 与 api_keys.key_hash 比对
错误响应:    401 {"code": "AUTH_FAIL", ...}，不暴露细节
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from meet_transcribe.config.loader import load_config
from meet_transcribe.db.models import ApiKey, Tenant
from meet_transcribe.db.session import get_db
from meet_transcribe.observability.metrics import AUTH_FAILURES

from .crypto import hash_api_key

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    name: str
    quota_concurrent: int


async def require_tenant(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        AUTH_FAILURES.labels(reason="missing").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_FAIL", "message": "missing bearer token"},
        )

    cfg = load_config()
    server_secret = cfg.secrets.server_secret.get_secret_value().encode("utf-8")
    if not server_secret:
        AUTH_FAILURES.labels(reason="server_secret_missing").inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "INTERNAL", "message": "server not initialized"},
        )

    key_hash = hash_api_key(creds.credentials, server_secret)

    stmt = (
        select(ApiKey, Tenant)
        .join(Tenant, Tenant.id == ApiKey.tenant_id)
        .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
        .where(Tenant.deleted_at.is_(None))
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        AUTH_FAILURES.labels(reason="not_found").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_FAIL", "message": "invalid api key"},
        )

    _api_key, tenant = row
    return TenantContext(
        tenant_id=tenant.id,
        name=tenant.name,
        quota_concurrent=tenant.quota_concurrent,
    )
