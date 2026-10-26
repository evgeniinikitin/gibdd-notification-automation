from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile, status

from .config import Settings


async def persist_upload(
    upload: UploadFile,
    *,
    tenant_id: str,
    settings: Settings,
) -> tuple[str, int, str]:
    now = datetime.now(timezone.utc)
    suffix = Path(upload.filename or "").suffix.lower()[:16]
    storage_key = (
        f"{tenant_id}/{now:%Y/%m}/{uuid.uuid4().hex}{suffix}"
    )
    destination = resolve_storage_path(storage_key, settings=settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(destination, "wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"Файл превышает лимит {settings.max_upload_size_mb} МБ"
                        ),
                    )
                digest.update(chunk)
                await output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    if size == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Пустой файл")
    return storage_key, size, digest.hexdigest()


def resolve_storage_path(storage_key: str, *, settings: Settings) -> Path:
    root = settings.storage_root.resolve()
    candidate = (root / storage_key).resolve()
    if root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Недопустимый путь к файлу")
    return candidate


def delete_storage_object(storage_key: str, *, settings: Settings) -> None:
    resolve_storage_path(storage_key, settings=settings).unlink(missing_ok=True)

