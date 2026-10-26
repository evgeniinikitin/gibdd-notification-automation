from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings
from .database import get_db
from .models import Tenant, User
from .security import decode_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Bearer"},
        )
    settings: Settings = request.app.state.settings
    payload = decode_token(credentials.credentials, expected_type="access", settings=settings)
    user = (
        await db.execute(
            select(User)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(User.id == payload["sub"], Tenant.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or user.tenant_id != payload.get("tenant_id")
        or user.token_version != payload.get("ver")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия недействительна",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*roles: str):
    async def role_dependency(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав",
            )
        return user

    return role_dependency


CurrentUser = Annotated[User, Depends(get_current_user)]
ProfileManager = Annotated[
    User,
    Depends(require_roles("client_admin", "support_admin", "admin")),
]
SystemOperator = Annotated[
    User,
    Depends(require_roles("support_admin", "admin")),
]
SystemAdmin = Annotated[
    User,
    Depends(require_roles("admin")),
]
DBSession = Annotated[AsyncSession, Depends(get_db)]
