from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent, User


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return request.client.host[:64] if request.client else None


def add_audit_event(
    db: AsyncSession,
    *,
    user: User,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: dict | None = None,
    request: Request | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        ip_address=client_ip(request),
    )
    db.add(event)
    return event

