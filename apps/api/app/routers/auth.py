from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ..audit import add_audit_event
from ..config import Settings
from ..dependencies import CurrentUser, DBSession, ProfileManager
from ..models import BalanceAccount, Tenant, User
from ..schemas import (
    LoginRequest,
    ProfileSettingsUpdate,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserRead,
)
from ..security import create_token_pair, decode_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


async def _tenant_name_conflict(
    db: DBSession,
    name: str,
    *,
    exclude_tenant_id: str | None = None,
) -> str | None:
    query = select(Tenant.id, Tenant.name)
    if exclude_tenant_id is not None:
        query = query.where(Tenant.id != exclude_tenant_id)
    normalized = name.casefold()
    return next(
        (
            tenant_id
            for tenant_id, existing_name in (await db.execute(query)).all()
            if existing_name.casefold() == normalized
        ),
        None,
    )


def _token_response(user: User, settings: Settings) -> TokenPair:
    access, refresh = create_token_pair(user, settings)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    db: DBSession,
) -> TokenPair:
    email = str(payload.email).lower()
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с таким email уже зарегистрирован",
        )
    if await _tenant_name_conflict(db, payload.company_name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Компания с таким названием уже зарегистрирована",
        )

    tenant = Tenant(name=payload.company_name)
    db.add(tenant)
    try:
        await db.flush()
        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=payload.full_name,
            password_hash=hash_password(payload.password),
            role="client_admin",
            last_login_at=datetime.now(timezone.utc),
        )
        account = BalanceAccount(tenant_id=tenant.id)
        db.add_all((user, account))
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Компания или email уже зарегистрированы",
        ) from exc
    add_audit_event(
        db,
        user=user,
        action="auth.register",
        entity_type="user",
        entity_id=user.id,
        details={"company_name": tenant.name},
        request=request,
    )
    await db.commit()
    return _token_response(user, request.app.state.settings)


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    db: DBSession,
) -> TokenPair:
    result = await db.execute(
        select(User)
        .options(selectinload(User.tenant))
        .where(User.email == payload.email.lower())
    )
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or user.tenant is None
        or not user.tenant.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user.last_login_at = datetime.now(timezone.utc)
    add_audit_event(
        db,
        user=user,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    await db.commit()
    return _token_response(user, request.app.state.settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: DBSession,
) -> TokenPair:
    settings: Settings = request.app.state.settings
    claims = decode_token(payload.refresh_token, expected_type="refresh", settings=settings)
    user = (
        await db.execute(
            select(User)
            .options(selectinload(User.tenant))
            .where(User.id == claims["sub"])
        )
    ).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or user.tenant is None
        or not user.tenant.is_active
        or user.tenant_id != claims.get("tenant_id")
        or user.token_version != claims.get("ver")
    ):
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    user.token_version += 1
    add_audit_event(
        db,
        user=user,
        action="auth.refresh",
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    await db.commit()
    return _token_response(user, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> None:
    user.token_version += 1
    add_audit_event(
        db,
        user=user,
        action="auth.logout",
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    await db.commit()


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser, db: DBSession) -> User:
    result = await db.execute(
        select(User).options(selectinload(User.tenant)).where(User.id == user.id)
    )
    return result.scalar_one()


@router.put("/me", response_model=UserRead)
async def update_me(
    payload: ProfileSettingsUpdate,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> User:
    current = (
        await db.execute(
            select(User).options(selectinload(User.tenant)).where(User.id == user.id)
        )
    ).scalar_one()
    conflict = await _tenant_name_conflict(
        db,
        payload.company_name,
        exclude_tenant_id=current.tenant_id,
    )
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Компания с таким названием уже зарегистрирована",
        )

    old_full_name = current.full_name
    old_company_name = current.tenant.name
    current.full_name = payload.full_name
    current.tenant.name = payload.company_name
    add_audit_event(
        db,
        user=current,
        action="profile.settings.updated",
        entity_type="user",
        entity_id=current.id,
        details={
            "full_name": {"before": old_full_name, "after": current.full_name},
            "company_name": {
                "before": old_company_name,
                "after": current.tenant.name,
            },
        },
        request=request,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Компания с таким названием уже зарегистрирована",
        ) from exc
    return (
        await db.execute(
            select(User).options(selectinload(User.tenant)).where(User.id == current.id)
        )
    ).scalar_one()
