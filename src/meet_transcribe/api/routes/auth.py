"""POST /v1/auth/ticket — 用 API Key 换一次性 ticket。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from meet_transcribe.auth.api_key import TenantContext, require_tenant
from meet_transcribe.auth.ticket import issue
from meet_transcribe.config.loader import load_config
from meet_transcribe.db.models import AuditLog
from meet_transcribe.db.session import get_db

router = APIRouter(tags=["auth"])


class TicketRequest(BaseModel):
    session_hint: str | None = None


class TicketResponse(BaseModel):
    ticket: str
    expires_in: int


@router.post("/ticket", response_model=TicketResponse)
async def create_ticket(
    body: TicketRequest,
    req: Request,
    tenant: TenantContext = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    cfg = load_config()
    ttl = cfg.server.ticket_ttl_seconds
    secret = cfg.secrets.server_secret.get_secret_value().encode("utf-8")

    token = issue(tenant.tenant_id, ttl, secret)

    db.add(
        AuditLog(
            tenant_id=tenant.tenant_id,
            actor=tenant.name,
            action="ticket.issue",
            resource_type="ticket",
            detail_json={"session_hint": body.session_hint, "ttl": ttl},
            ip=str(req.client.host) if req.client else None,
        )
    )
    await db.commit()

    return TicketResponse(ticket=token, expires_in=ttl)
