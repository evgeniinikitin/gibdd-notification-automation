from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from ..audit import add_audit_event
from ..dependencies import CurrentUser, DBSession, ProfileManager
from ..models import (
    CarrierProfile,
    Driver,
    StoredFile,
    TenantAsset,
    Vehicle,
)
from ..schemas import (
    AssetRead,
    CarrierProfilePatch,
    CarrierProfileRead,
    CarrierProfileWrite,
    OnboardingRequirement,
    OnboardingStatus,
)
from ..storage import delete_storage_object, persist_upload, resolve_storage_path

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

CARRIER_REQUIRED_FIELDS = (
    "legal_name",
    "inn",
    "legal_address",
    "phone",
    "email",
    "director_position",
    "director_full_name",
)


async def build_onboarding_status(db: DBSession, tenant_id: str) -> OnboardingStatus:
    profile = (
        await db.execute(select(CarrierProfile).where(CarrierProfile.tenant_id == tenant_id))
    ).scalar_one_or_none()
    profile_complete = bool(
        profile
        and all(getattr(profile, field, None) for field in CARRIER_REQUIRED_FIELDS)
    )
    signature = (
        await db.execute(
            select(TenantAsset.id).where(
                TenantAsset.tenant_id == tenant_id, TenantAsset.kind == "signature"
            )
        )
    ).scalar_one_or_none()
    vehicle = (
        await db.execute(
            select(Vehicle.id).where(
                Vehicle.tenant_id == tenant_id, Vehicle.is_active.is_(True)
            )
        )
    ).first()
    driver = (
        await db.execute(
            select(Driver.id).where(
                Driver.tenant_id == tenant_id, Driver.is_active.is_(True)
            )
        )
    ).first()
    values = [
        ("carrier", "Реквизиты перевозчика", profile_complete, "/settings/carrier"),
        ("signature", "Подпись PNG", bool(signature), "/settings/documents"),
        ("vehicle", "Хотя бы один автобус", bool(vehicle), "/settings/vehicles"),
        ("driver", "Хотя бы один водитель", bool(driver), "/settings/drivers"),
    ]
    requirements = [
        OnboardingRequirement(key=key, label=label, completed=completed, path=path)
        for key, label, completed, path in values
    ]
    complete_count = sum(item.completed for item in requirements)
    return OnboardingStatus(
        completed=complete_count == len(requirements),
        completion_percent=round(complete_count / len(requirements) * 100),
        requirements=requirements,
    )


@router.get("/status", response_model=OnboardingStatus)
async def onboarding_status(user: CurrentUser, db: DBSession) -> OnboardingStatus:
    return await build_onboarding_status(db, user.tenant_id)


@router.post("/complete", response_model=OnboardingStatus)
async def complete_onboarding(user: ProfileManager, db: DBSession) -> OnboardingStatus:
    result = await build_onboarding_status(db, user.tenant_id)
    if not result.completed:
        missing = ", ".join(
            item.label for item in result.requirements if not item.completed
        )
        raise HTTPException(
            status_code=409,
            detail=f"Заполните обязательные разделы: {missing}",
        )
    return result


@router.get("/carrier", response_model=CarrierProfileRead | None)
async def get_carrier(user: CurrentUser, db: DBSession) -> CarrierProfile | None:
    return (
        await db.execute(
            select(CarrierProfile).where(CarrierProfile.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()


@router.put("/carrier", response_model=CarrierProfileRead)
async def upsert_carrier(
    payload: CarrierProfileWrite,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> CarrierProfile:
    profile = (
        await db.execute(
            select(CarrierProfile).where(CarrierProfile.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    action = "carrier.updated"
    if profile is None:
        profile = CarrierProfile(tenant_id=user.tenant_id)
        db.add(profile)
        action = "carrier.created"
    for key, value in payload.model_dump().items():
        setattr(profile, key, value)
    await db.flush()
    add_audit_event(
        db,
        user=user,
        action=action,
        entity_type="carrier_profile",
        entity_id=profile.id,
        request=request,
    )
    await db.commit()
    await db.refresh(profile)
    return profile


@router.patch("/carrier", response_model=CarrierProfileRead)
async def patch_carrier(
    payload: CarrierProfilePatch,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> CarrierProfile:
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="Нет данных для сохранения")
    profile = (
        await db.execute(
            select(CarrierProfile).where(CarrierProfile.tenant_id == user.tenant_id)
        )
    ).scalar_one_or_none()
    action = "carrier.updated"
    if profile is None:
        profile = CarrierProfile(tenant_id=user.tenant_id)
        db.add(profile)
        action = "carrier.created"
    for key, value in values.items():
        setattr(profile, key, value)
    await db.flush()
    add_audit_event(
        db,
        user=user,
        action=action,
        entity_type="carrier_profile",
        entity_id=profile.id,
        details={"updated_fields": sorted(values)},
        request=request,
    )
    await db.commit()
    await db.refresh(profile)
    return profile


async def _create_stored_file(
    *,
    upload: UploadFile,
    kind: str,
    request: Request,
    user,
    db,
) -> StoredFile:
    settings = request.app.state.settings
    original_name = Path(upload.filename or "file").name
    content_type = upload.content_type or "application/octet-stream"
    storage_key, size, checksum = await persist_upload(
        upload, tenant_id=user.tenant_id, settings=settings
    )
    stored = StoredFile(
        tenant_id=user.tenant_id,
        uploaded_by_id=user.id,
        kind=kind,
        original_name=original_name,
        storage_key=storage_key,
        content_type=content_type,
        size=size,
        checksum_sha256=checksum,
    )
    db.add(stored)
    await db.flush()
    return stored


def _validate_profile_png(storage_key: str, *, settings) -> None:
    path = resolve_storage_path(storage_key, settings=settings)
    if path.stat().st_size > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PNG превышает лимит 10 МБ")
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise HTTPException(
                    status_code=422,
                    detail="Содержимое файла не является PNG",
                )
            width, height = image.size
            if width < 32 or height < 16 or width > 4096 or height > 4096:
                raise HTTPException(
                    status_code=422,
                    detail="Недопустимый размер изображения",
                )
            rgba = image.convert("RGBA")
            alpha_min, _alpha_max = rgba.getchannel("A").getextrema()
            if alpha_min == 255:
                raise HTTPException(
                    status_code=422,
                    detail="PNG должен иметь прозрачный фон",
                )
            rgba.load()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Поврежденный или неподдерживаемый PNG",
        ) from exc


@router.post("/assets/{kind}", response_model=AssetRead)
async def upload_asset(
    kind: str,
    request: Request,
    user: ProfileManager,
    db: DBSession,
    file: UploadFile = File(...),
) -> TenantAsset:
    if kind not in {"signature", "stamp"}:
        raise HTTPException(status_code=404, detail="Неизвестный тип изображения")
    if Path(file.filename or "").suffix.lower() != ".png":
        raise HTTPException(status_code=422, detail="Поддерживается только PNG")
    current = (
        await db.execute(
            select(TenantAsset, StoredFile)
            .join(StoredFile, StoredFile.id == TenantAsset.file_id)
            .where(TenantAsset.tenant_id == user.tenant_id, TenantAsset.kind == kind)
        )
    ).first()
    stored = await _create_stored_file(
        upload=file, kind=kind, request=request, user=user, db=db
    )
    try:
        _validate_profile_png(
            stored.storage_key,
            settings=request.app.state.settings,
        )
    except HTTPException:
        delete_storage_object(
            stored.storage_key,
            settings=request.app.state.settings,
        )
        await db.rollback()
        raise
    if current:
        asset, previous_file = current
        await db.delete(asset)
        await db.flush()
        await db.delete(previous_file)
        delete_storage_object(
            previous_file.storage_key, settings=request.app.state.settings
        )
    asset = TenantAsset(
        tenant_id=user.tenant_id,
        file_id=stored.id,
        kind=kind,
    )
    db.add(asset)
    await db.flush()
    add_audit_event(
        db,
        user=user,
        action=f"asset.{kind}.uploaded",
        entity_type="tenant_asset",
        entity_id=asset.id,
        request=request,
    )
    await db.commit()
    await db.refresh(asset)
    return asset


@router.get("/assets", response_model=list[AssetRead])
async def list_assets(user: CurrentUser, db: DBSession) -> list[TenantAsset]:
    return list(
        (
            await db.scalars(
                select(TenantAsset)
                .where(TenantAsset.tenant_id == user.tenant_id)
                .order_by(TenantAsset.kind)
            )
        ).all()
    )
