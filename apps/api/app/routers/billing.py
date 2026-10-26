from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit import add_audit_event
from ..dependencies import CurrentUser, DBSession
from ..models import BalanceAccount, BillingTransaction, Trip, User
from ..schemas import (
    BalanceAdjustment,
    BalanceRead,
    TransactionRead,
)

router = APIRouter(prefix="/billing", tags=["billing"])


async def _account(db, tenant_id: str, lock: bool = False) -> BalanceAccount:
    query = select(BalanceAccount).where(BalanceAccount.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update()
    account = (await db.execute(query)).scalar_one_or_none()
    if account is None:
        account = BalanceAccount(tenant_id=tenant_id)
        db.add(account)
        await db.flush()
    return account


@router.get("/balance", response_model=BalanceRead)
async def get_balance(user: CurrentUser, db: DBSession) -> BalanceRead:
    account = await _account(db, user.tenant_id)
    return BalanceRead(
        available=account.available,
        reserved=account.reserved,
        total=account.available + account.reserved,
    )


@router.get("/transactions", response_model=list[TransactionRead])
async def list_transactions(
    user: CurrentUser,
    db: DBSession,
    limit: int = 100,
) -> list[TransactionRead]:
    rows = (
        await db.execute(
            select(
                BillingTransaction,
                User.full_name,
                User.email,
                Trip.reference,
            )
            .outerjoin(User, User.id == BillingTransaction.actor_id)
            .outerjoin(Trip, Trip.id == BillingTransaction.trip_id)
            .where(BillingTransaction.tenant_id == user.tenant_id)
            .order_by(BillingTransaction.created_at.desc())
            .limit(min(limit, 200))
        )
    ).all()
    return [
        TransactionRead(
            id=transaction.id,
            kind=transaction.kind,
            amount=transaction.amount,
            balance_after=transaction.balance_after,
            note=transaction.note,
            trip_id=transaction.trip_id,
            trip_reference=trip_reference,
            actor_id=transaction.actor_id,
            actor_name=actor_name,
            actor_email=actor_email,
            created_at=transaction.created_at,
        )
        for transaction, actor_name, actor_email, trip_reference in rows
    ]


@router.post("/adjustments", response_model=TransactionRead, status_code=201)
async def adjust_balance(
    payload: BalanceAdjustment,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> BillingTransaction:
    if user.role not in {"admin", "support_admin"}:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    account = await _account(db, user.tenant_id, lock=True)
    new_balance = account.available + payload.amount
    if new_balance < 0:
        raise HTTPException(status_code=409, detail="Недостаточно средств")
    account.available = new_balance
    transaction = BillingTransaction(
        tenant_id=user.tenant_id,
        account_id=account.id,
        actor_id=user.id,
        kind="manual_credit" if payload.amount > 0 else "manual_debit",
        amount=payload.amount,
        balance_after=new_balance,
        note=payload.note,
        idempotency_key=payload.idempotency_key,
    )
    db.add(transaction)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Операция уже выполнена") from exc
    add_audit_event(
        db,
        user=user,
        action="billing.adjusted",
        entity_type="billing_transaction",
        entity_id=transaction.id,
        details={"amount": str(payload.amount)},
        request=request,
    )
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def reserve_notification(
    db,
    *,
    tenant_id: str,
    actor_id: str | None,
    trip_id: str,
    idempotency_key: str,
) -> BillingTransaction:
    existing = await db.scalar(
        select(BillingTransaction).where(
            BillingTransaction.idempotency_key == idempotency_key,
            BillingTransaction.tenant_id == tenant_id,
        )
    )
    if existing:
        return existing
    account = await _account(db, tenant_id, lock=True)
    unit = Decimal("1")
    if account.available < unit:
        raise HTTPException(status_code=409, detail="Недостаточный баланс уведомлений")
    account.available -= unit
    account.reserved += unit
    transaction = BillingTransaction(
        tenant_id=tenant_id,
        account_id=account.id,
        actor_id=actor_id,
        trip_id=trip_id,
        kind="reserve",
        amount=-unit,
        balance_after=account.available,
        note="Резерв уведомления",
        idempotency_key=idempotency_key,
    )
    db.add(transaction)
    await db.flush()
    return transaction


async def settle_reservation(
    db,
    *,
    tenant_id: str,
    actor_id: str | None,
    trip_id: str,
    success: bool,
    idempotency_key: str,
) -> BillingTransaction:
    existing = await db.scalar(
        select(BillingTransaction).where(
            BillingTransaction.idempotency_key == idempotency_key,
            BillingTransaction.tenant_id == tenant_id,
        )
    )
    if existing:
        return existing
    account = await _account(db, tenant_id, lock=True)
    unit = Decimal("1")
    if account.reserved < unit:
        raise HTTPException(status_code=409, detail="Резерв не найден")
    account.reserved -= unit
    amount = Decimal("0")
    kind = "capture"
    if not success:
        account.available += unit
        amount = unit
        kind = "release"
    transaction = BillingTransaction(
        tenant_id=tenant_id,
        account_id=account.id,
        actor_id=actor_id,
        trip_id=trip_id,
        kind=kind,
        amount=amount,
        balance_after=account.available,
        note="Списание уведомления" if success else "Возврат резерва",
        idempotency_key=idempotency_key,
    )
    db.add(transaction)
    await db.flush()
    return transaction
