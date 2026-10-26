from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..dependencies import CurrentUser, DBSession
from ..models import AuditEvent
from ..schemas import AuditEventRead

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventRead])
async def list_audit_events(
    user: CurrentUser,
    db: DBSession,
    action: str | None = None,
    entity_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent]:
    if user.role not in {"admin", "support_admin", "auditor", "client_admin"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    query = select(AuditEvent).where(AuditEvent.tenant_id == user.tenant_id)
    if action:
        query = query.where(AuditEvent.action == action)
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    query = (
        query.order_by(AuditEvent.created_at.desc())
        .offset(max(offset, 0))
        .limit(min(limit, 500))
    )
    return list((await db.scalars(query)).all())

