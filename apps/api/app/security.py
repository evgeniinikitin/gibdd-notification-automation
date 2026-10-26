from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, status

from .config import Settings

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        valid = password_hasher.verify(encoded, password)
        return bool(valid)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _encode_token(
    *,
    subject: str,
    tenant_id: str,
    role: str,
    token_type: str,
    token_version: int,
    expires_delta: timedelta,
    settings: Settings,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "typ": token_type,
        "ver": token_version,
        "jti": secrets.token_urlsafe(18),
        "iat": now,
        "nbf": now,
        "exp": now + expires_delta,
        "iss": "buscript-api",
        "aud": "buscript",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def create_token_pair(user, settings: Settings) -> tuple[str, str]:
    access_token = _encode_token(
        subject=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        token_type="access",
        token_version=user.token_version,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
        settings=settings,
    )
    refresh_token = _encode_token(
        subject=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        token_type="refresh",
        token_version=user.token_version,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
        settings=settings,
    )
    return access_token, refresh_token


def decode_token(token: str, *, expected_type: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[JWT_ALGORITHM],
            audience="buscript",
            issuer="buscript-api",
            options={"require": ["sub", "tenant_id", "typ", "ver", "iat", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или истекший токен",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if payload.get("typ") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный тип токена",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def hash_token_for_log(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

