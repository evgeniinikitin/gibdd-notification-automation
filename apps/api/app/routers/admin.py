from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from ..audit import client_ip
from ..dependencies import DBSession, SystemAdmin
from ..models import (
    AuditEvent,
    BalanceAccount,
    BillingTransaction,
    CarrierProfile,
    Tenant,
    Trip,
    User,
)
from ..schemas import (
    AdminNotificationBalanceResult,
    AdminNotificationBalanceUpdate,
    AdminTenantRead,
)
from .onboarding import CARRIER_REQUIRED_FIELDS, build_onboarding_status

router = APIRouter(prefix="/admin", tags=["admin"])


async def _primary_admin(db: DBSession, tenant_id: str) -> User | None:
    users = list(
        (
            await db.scalars(
                select(User)
                .where(
                    User.tenant_id == tenant_id,
                    User.role.in_(("client_admin", "admin")),
                )
                .order_by(User.created_at.asc())
            )
        ).all()
    )
    return next((user for user in users if user.role == "client_admin"), None) or (
        users[0] if users else None
    )


async def _tenant_summary(db: DBSession, tenant: Tenant) -> AdminTenantRead:
    primary_admin = await _primary_admin(db, tenant.id)
    profile = await db.scalar(
        select(CarrierProfile).where(CarrierProfile.tenant_id == tenant.id)
    )
    profile_completed = bool(
        profile
        and all(getattr(profile, field, None) for field in CARRIER_REQUIRED_FIELDS)
    )
    onboarding = await build_onboarding_status(db, tenant.id)
    account = await db.scalar(
        select(BalanceAccount).where(BalanceAccount.tenant_id == tenant.id)
    )
    trip_count = int(
        await db.scalar(
            select(func.count(Trip.id)).where(Trip.tenant_id == tenant.id)
        )
        or 0
    )
    submitted_trip_count = int(
        await db.scalar(
            select(func.count(Trip.id)).where(
                Trip.tenant_id == tenant.id,
                Trip.status == "submitted",
            )
        )
        or 0
    )
    return AdminTenantRead(
        tenant_id=tenant.id,
        company_name=tenant.name,
        primary_admin_email=primary_admin.email if primary_admin else None,
        primary_admin_full_name=primary_admin.full_name if primary_admin else None,
        is_active=tenant.is_active,
        profile_completed=profile_completed,
        onboarding_completed=onboarding.completed,
        onboarding_completion_percent=onboarding.completion_percent,
        available_notifications=int(account.available) if account else 0,
        reserved_notifications=int(account.reserved) if account else 0,
        trip_count=trip_count,
        submitted_trip_count=submitted_trip_count,
        created_at=tenant.created_at,
        updated_at=tenant.updated_at,
    )


@router.get("/tenants", response_model=list[AdminTenantRead])
async def list_tenants(
    _user: SystemAdmin,
    db: DBSession,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AdminTenantRead]:
    tenants = list(
        (
            await db.scalars(
                select(Tenant)
                .where(Tenant.is_system.is_(False))
                .order_by(Tenant.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    return [await _tenant_summary(db, tenant) for tenant in tenants]


@router.patch(
    "/tenants/{tenant_id}/balance",
    response_model=AdminNotificationBalanceResult,
)
async def set_notification_balance(
    tenant_id: str,
    payload: AdminNotificationBalanceUpdate,
    request: Request,
    user: SystemAdmin,
    db: DBSession,
) -> AdminNotificationBalanceResult:
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Компания не найдена",
        )

    account = await db.scalar(
        select(BalanceAccount)
        .where(BalanceAccount.tenant_id == tenant_id)
        .with_for_update()
    )
    if account is None:
        account = BalanceAccount(tenant_id=tenant_id)
        db.add(account)
        await db.flush()

    before = account.available
    after = Decimal(payload.available_notifications)
    delta = after - before
    account.available = after
    note = (payload.note or "").strip() or "Установлено системным администратором"
    transaction = BillingTransaction(
        tenant_id=tenant_id,
        account_id=account.id,
        actor_id=user.id,
        kind="admin_set_balance",
        amount=delta,
        balance_after=after,
        note=note,
    )
    db.add(transaction)
    await db.flush()
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_id=user.id,
            action="admin.notification_balance.set",
            entity_type="balance_account",
            entity_id=account.id,
            details={
                "before": str(before),
                "after": str(after),
                "delta": str(delta),
                "note": note,
                "actor_tenant_id": user.tenant_id,
            },
            ip_address=client_ip(request),
        )
    )
    await db.commit()
    await db.refresh(account)
    return AdminNotificationBalanceResult(
        tenant_id=tenant_id,
        available_notifications=int(account.available),
        reserved_notifications=int(account.reserved),
        delta=int(delta),
        transaction_id=transaction.id,
        updated_at=account.updated_at,
    )
