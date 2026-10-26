from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit import add_audit_event
from ..dependencies import CurrentUser, DBSession, ProfileManager
from ..models import Driver, Vehicle
from ..schemas import DriverRead, DriverWrite, VehicleRead, VehicleWrite

router = APIRouter(tags=["fleet"])


async def _tenant_entity(db, model, entity_id: str, tenant_id: str):
    entity = (
        await db.execute(
            select(model).where(model.id == entity_id, model.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if entity is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return entity


@router.get("/vehicles", response_model=list[VehicleRead])
async def list_vehicles(
    user: CurrentUser,
    db: DBSession,
    include_inactive: bool = False,
) -> list[Vehicle]:
    query = select(Vehicle).where(Vehicle.tenant_id == user.tenant_id)
    if not include_inactive:
        query = query.where(Vehicle.is_active.is_(True))
    return list((await db.scalars(query.order_by(Vehicle.registration_plate))).all())


@router.post("/vehicles", response_model=VehicleRead, status_code=201)
async def create_vehicle(
    payload: VehicleWrite,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> Vehicle:
    vehicle = Vehicle(tenant_id=user.tenant_id, **payload.model_dump())
    db.add(vehicle)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Автобус с таким госномером уже есть") from exc
    add_audit_event(
        db,
        user=user,
        action="vehicle.created",
        entity_type="vehicle",
        entity_id=vehicle.id,
        request=request,
    )
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.get("/vehicles/{vehicle_id}", response_model=VehicleRead)
async def get_vehicle(vehicle_id: str, user: CurrentUser, db: DBSession) -> Vehicle:
    return await _tenant_entity(db, Vehicle, vehicle_id, user.tenant_id)


@router.put("/vehicles/{vehicle_id}", response_model=VehicleRead)
async def update_vehicle(
    vehicle_id: str,
    payload: VehicleWrite,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> Vehicle:
    vehicle = await _tenant_entity(db, Vehicle, vehicle_id, user.tenant_id)
    for field, value in payload.model_dump().items():
        setattr(vehicle, field, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Автобус с таким госномером уже есть") from exc
    add_audit_event(
        db,
        user=user,
        action="vehicle.updated",
        entity_type="vehicle",
        entity_id=vehicle.id,
        request=request,
    )
    await db.commit()
    await db.refresh(vehicle)
    return vehicle


@router.delete("/vehicles/{vehicle_id}", status_code=204)
async def deactivate_vehicle(
    vehicle_id: str,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> None:
    vehicle = await _tenant_entity(db, Vehicle, vehicle_id, user.tenant_id)
    vehicle.is_active = False
    add_audit_event(
        db,
        user=user,
        action="vehicle.deactivated",
        entity_type="vehicle",
        entity_id=vehicle.id,
        request=request,
    )
    await db.commit()


@router.get("/drivers", response_model=list[DriverRead])
async def list_drivers(
    user: CurrentUser,
    db: DBSession,
    include_inactive: bool = False,
) -> list[Driver]:
    query = select(Driver).where(Driver.tenant_id == user.tenant_id)
    if not include_inactive:
        query = query.where(Driver.is_active.is_(True))
    return list((await db.scalars(query.order_by(Driver.full_name))).all())


@router.post("/drivers", response_model=DriverRead, status_code=201)
async def create_driver(
    payload: DriverWrite,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> Driver:
    driver = Driver(tenant_id=user.tenant_id, **payload.model_dump())
    db.add(driver)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Водитель с таким удостоверением уже есть"
        ) from exc
    add_audit_event(
        db,
        user=user,
        action="driver.created",
        entity_type="driver",
        entity_id=driver.id,
        request=request,
    )
    await db.commit()
    await db.refresh(driver)
    return driver


@router.get("/drivers/{driver_id}", response_model=DriverRead)
async def get_driver(driver_id: str, user: CurrentUser, db: DBSession) -> Driver:
    return await _tenant_entity(db, Driver, driver_id, user.tenant_id)


@router.put("/drivers/{driver_id}", response_model=DriverRead)
async def update_driver(
    driver_id: str,
    payload: DriverWrite,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> Driver:
    driver = await _tenant_entity(db, Driver, driver_id, user.tenant_id)
    for field, value in payload.model_dump().items():
        setattr(driver, field, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Водитель с таким удостоверением уже есть"
        ) from exc
    add_audit_event(
        db,
        user=user,
        action="driver.updated",
        entity_type="driver",
        entity_id=driver.id,
        request=request,
    )
    await db.commit()
    await db.refresh(driver)
    return driver


@router.delete("/drivers/{driver_id}", status_code=204)
async def deactivate_driver(
    driver_id: str,
    request: Request,
    user: ProfileManager,
    db: DBSession,
) -> None:
    driver = await _tenant_entity(db, Driver, driver_id, user.tenant_id)
    driver.is_active = False
    add_audit_event(
        db,
        user=user,
        action="driver.deactivated",
        entity_type="driver",
        entity_id=driver.id,
        request=request,
    )
    await db.commit()
