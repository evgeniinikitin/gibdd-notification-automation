from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from ..audit import add_audit_event
from ..dependencies import CurrentUser, DBSession
from ..models import Conversation, StoredFile, Trip
from ..schemas import FileRead
from ..storage import persist_upload, resolve_storage_path

router = APIRouter(prefix="/files", tags=["files"])

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".webp",
    ".zip",
}

ARCHIVE_KINDS = {"notification", "receipt"}


def _file_read(stored: StoredFile, trip_reference: str | None = None) -> FileRead:
    return FileRead.model_validate(stored).model_copy(
        update={"trip_reference": trip_reference}
    )


async def _trip_reference(db, trip_id: str | None) -> str | None:
    if trip_id is None:
        return None
    return await db.scalar(select(Trip.reference).where(Trip.id == trip_id))


async def _stored_file(db, file_id: str, tenant_id: str) -> StoredFile:
    stored = (
        await db.execute(
            select(StoredFile).where(
                StoredFile.id == file_id,
                StoredFile.tenant_id == tenant_id,
                StoredFile.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()
    if stored is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return stored


async def _validate_parent(db, model, entity_id: str | None, tenant_id: str, label: str):
    if entity_id is None:
        return
    exists = await db.scalar(
        select(model.id).where(model.id == entity_id, model.tenant_id == tenant_id)
    )
    if exists is None:
        raise HTTPException(status_code=422, detail=f"{label} недоступен")


@router.post("", response_model=FileRead, status_code=201)
async def upload_file(
    request: Request,
    user: CurrentUser,
    db: DBSession,
    file: UploadFile = File(...),
    conversation_id: str | None = Form(None),
    trip_id: str | None = Form(None),
) -> FileRead:
    original_name = Path(file.filename or "file").name
    if Path(original_name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Неподдерживаемый формат файла")
    await _validate_parent(
        db, Conversation, conversation_id, user.tenant_id, "Диалог"
    )
    await _validate_parent(db, Trip, trip_id, user.tenant_id, "Заявка")
    storage_key, size, checksum = await persist_upload(
        file, tenant_id=user.tenant_id, settings=request.app.state.settings
    )
    stored = StoredFile(
        tenant_id=user.tenant_id,
        uploaded_by_id=user.id,
        conversation_id=conversation_id,
        trip_id=trip_id,
        # User uploads are always source attachments. Generated notifications
        # and receipts are created only by the system pipeline.
        kind="attachment",
        original_name=original_name,
        storage_key=storage_key,
        content_type=file.content_type or "application/octet-stream",
        size=size,
        checksum_sha256=checksum,
    )
    db.add(stored)
    await db.flush()
    add_audit_event(
        db,
        user=user,
        action="file.uploaded",
        entity_type="stored_file",
        entity_id=stored.id,
        details={"size": size, "kind": "attachment"},
        request=request,
    )
    await db.commit()
    await db.refresh(stored)
    return _file_read(stored, await _trip_reference(db, stored.trip_id))


@router.get("", response_model=list[FileRead])
async def list_files(
    user: CurrentUser,
    db: DBSession,
    conversation_id: str | None = None,
    trip_id: str | None = None,
    kind: str | None = None,
    archive_only: bool = True,
    limit: int = 100,
) -> list[FileRead]:
    query = select(StoredFile, Trip.reference).outerjoin(
        Trip, Trip.id == StoredFile.trip_id
    ).where(
        StoredFile.tenant_id == user.tenant_id,
        StoredFile.is_deleted.is_(False),
    )
    if conversation_id:
        query = query.where(StoredFile.conversation_id == conversation_id)
    if trip_id:
        query = query.where(StoredFile.trip_id == trip_id)
    if kind:
        if archive_only and kind not in ARCHIVE_KINDS:
            raise HTTPException(
                status_code=422,
                detail="В архиве доступны только уведомления и квитанции",
            )
        query = query.where(StoredFile.kind == kind)
    elif archive_only:
        query = query.where(StoredFile.kind.in_(ARCHIVE_KINDS))
    query = query.order_by(StoredFile.created_at.desc()).limit(min(limit, 200))
    return [
        _file_read(stored, trip_reference)
        for stored, trip_reference in (await db.execute(query)).all()
    ]


@router.get("/{file_id}", response_model=FileRead)
async def get_file_metadata(
    file_id: str, user: CurrentUser, db: DBSession
) -> FileRead:
    stored = await _stored_file(db, file_id, user.tenant_id)
    return _file_read(stored, await _trip_reference(db, stored.trip_id))


@router.get("/{file_id}/download", response_class=FileResponse)
async def download_file(
    file_id: str,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> FileResponse:
    stored = await _stored_file(db, file_id, user.tenant_id)
    path = resolve_storage_path(stored.storage_key, settings=request.app.state.settings)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="Файл отсутствует в хранилище")
    add_audit_event(
        db,
        user=user,
        action="file.downloaded",
        entity_type="stored_file",
        entity_id=stored.id,
        request=request,
    )
    await db.commit()
    return FileResponse(
        path,
        media_type=stored.content_type,
        filename=stored.original_name,
    )


@router.delete("/{file_id}", status_code=204)
async def delete_file(
    file_id: str,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> None:
    stored = await _stored_file(db, file_id, user.tenant_id)
    if stored.kind in {"signature", "stamp", "template", "notification", "receipt"}:
        raise HTTPException(
            status_code=409,
            detail="Системный документ удаляется через соответствующий раздел",
        )
    stored.is_deleted = True
    add_audit_event(
        db,
        user=user,
        action="file.soft_deleted",
        entity_type="stored_file",
        entity_id=stored.id,
        request=request,
    )
    await db.commit()
