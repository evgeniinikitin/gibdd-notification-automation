"""Persisted confirmed-trip -> document -> payload -> portal orchestration."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import (
    AuditEvent,
    CarrierProfile,
    Driver,
    Message,
    StoredFile,
    SubmissionAttempt,
    SubmissionJob,
    TenantAsset,
    Trip,
    TripPayload,
    Vehicle,
    utcnow,
)
from ..routers.billing import settle_reservation
from ..storage import resolve_storage_path
from .documents import DocumentGenerationService
from .exceptions import AutomationError
from .payload import PayloadBuilderService
from .schemas import (
    ApplicantDTO,
    BusDocumentRowDTO,
    CarrierProfileDTO,
    ChildDocumentRowDTO,
    CompanionDocumentRowDTO,
    DocumentGenerationRequestDTO,
    DriverDocumentRowDTO,
    DriverProfileDTO,
    GibddPayloadDTO,
    PartyDTO,
    PartyType,
    PayloadBuildInputDTO,
    RouteDTO,
    RouteStopDTO,
    SubmissionStatus,
    TransportationInputDTO,
    VehicleProfileDTO,
)
from .submission import (
    GibddPortalAdapter,
    RootStorageFileResolver,
    SubmissionWorker,
    SubmissionWorkerConfig,
)


class TripAutomationValidationError(AutomationError):
    """User-correctable validation failures discovered before generation."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(dict.fromkeys(missing))
        super().__init__("; ".join(self.missing))


@dataclass(slots=True)
class PreparedTrip:
    trip: Trip
    carrier: CarrierProfile
    carrier_dto: CarrierProfileDTO
    vehicle: Vehicle
    vehicle_dto: VehicleProfileDTO
    driver: Driver
    driver_dto: DriverProfileDTO
    vehicles: list[Vehicle]
    vehicle_dtos: list[VehicleProfileDTO]
    drivers: list[Driver]
    driver_dtos: list[DriverProfileDTO]
    customer: PartyDTO
    transportation: TransportationInputDTO
    route: RouteDTO
    children: list[ChildDocumentRowDTO]
    companions: list[CompanionDocumentRowDTO]
    signature_path: Path
    stamp_path: Path | None
    signature_file_id: str


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _required_text(value: Any, label: str, missing: list[str]) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    missing.append(label)
    return ""


def _optional_text(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _parse_date(value: Any, label: str, missing: list[str]) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    missing.append(label)
    return None


def _diagnostic_card_text(vehicle: Vehicle) -> str:
    assert vehicle.diagnostic_card_number
    assert vehicle.diagnostic_card_valid_until
    return (
        f"{vehicle.diagnostic_card_number} до "
        f"{vehicle.diagnostic_card_valid_until:%d.%m.%Y} г."
    )


async def ensure_submission_job(
    db: AsyncSession,
    *,
    trip: Trip,
    live_submission: bool,
) -> SubmissionJob:
    """Create the one logical job for a trip, preserving retry idempotency."""

    job = await db.scalar(
        select(SubmissionJob).where(
            SubmissionJob.trip_id == trip.id,
            SubmissionJob.tenant_id == trip.tenant_id,
        )
    )
    if job is None:
        job = SubmissionJob(
            tenant_id=trip.tenant_id,
            trip_id=trip.id,
            status="queued",
            live_submission=live_submission,
        )
        db.add(job)
        await db.flush()
    elif job.status not in {"dry_run_ready", "submitted"}:
        job.status = "queued"
        job.last_error = None
    return job


class ConfirmedTripOrchestrator:
    """Database-backed automation with immutable artifacts across retries."""

    def __init__(
        self,
        *,
        settings: Settings,
        document_service: DocumentGenerationService | None = None,
        payload_builder: PayloadBuilderService | None = None,
        submission_worker: SubmissionWorker | None = None,
    ) -> None:
        self.settings = settings
        self.documents = document_service or DocumentGenerationService()
        self.payloads = payload_builder or PayloadBuilderService()
        self.submission_worker = submission_worker

    async def prepare_confirmation(
        self, db: AsyncSession, trip: Trip
    ) -> PreparedTrip:
        """Validate every legal input and auto-select only an unambiguous fleet."""

        missing: list[str] = []
        data = _as_dict(trip.extracted_data)
        unresolved = _as_list(data.get("missing_clarifications"))
        for item in unresolved:
            detail = _as_dict(item)
            label = detail.get("label") or detail.get("field") or "уточнение"
            missing.append(f"Ответьте на уточнение: {label}")
        for field in _as_list(data.get("uncertain_fields")):
            missing.append(f"Подтвердите поле: {field}")

        route_data = _as_dict(data.get("route"))
        route_slot = _as_dict(_as_dict(data.get("slot_states")).get("route.text"))
        if (
            route_data.get("approximate") is True
            and route_slot.get("status") != "confirmed"
        ):
            missing.append("Подтвердите или исправьте приблизительный маршрут")

        carrier = await db.scalar(
            select(CarrierProfile).where(CarrierProfile.tenant_id == trip.tenant_id)
        )
        if carrier is None:
            raise TripAutomationValidationError(["Заполните профиль перевозчика"])
        for field, label in (
            ("legal_name", "Полное наименование перевозчика"),
            ("inn", "ИНН перевозчика"),
            ("legal_address", "Адрес перевозчика"),
            ("phone", "Телефон перевозчика"),
            ("email", "Email перевозчика"),
            ("director_position", "Должность подписанта"),
            ("director_full_name", "ФИО подписанта"),
        ):
            _required_text(getattr(carrier, field), label, missing)

        asset_rows = (
            await db.execute(
                select(TenantAsset, StoredFile)
                .join(StoredFile, StoredFile.id == TenantAsset.file_id)
                .where(
                    TenantAsset.tenant_id == trip.tenant_id,
                    StoredFile.tenant_id == trip.tenant_id,
                    StoredFile.is_deleted.is_(False),
                )
            )
        ).all()
        assets = {asset.kind: stored for asset, stored in asset_rows}
        signature = assets.get("signature")
        if signature is None:
            missing.append("Загрузите PNG-подпись в профиле")

        vehicles = list(
            (
                await db.scalars(
                    select(Vehicle).where(
                        Vehicle.tenant_id == trip.tenant_id,
                        Vehicle.is_active.is_(True),
                    )
                )
            ).all()
        )
        drivers = list(
            (
                await db.scalars(
                    select(Driver).where(
                        Driver.tenant_id == trip.tenant_id,
                        Driver.is_active.is_(True),
                    )
                )
            ).all()
        )
        if trip.selected_vehicle_id is None and len(vehicles) == 1:
            trip.selected_vehicle_id = vehicles[0].id
        if trip.selected_driver_id is None and len(drivers) == 1:
            trip.selected_driver_id = drivers[0].id

        assignments = _as_dict(data.get("transport_assignments"))
        assigned_vehicle_ids = _as_list(assignments.get("vehicle_ids")) or [
            trip.selected_vehicle_id
        ]
        assigned_driver_ids = _as_list(assignments.get("driver_ids")) or [
            trip.selected_driver_id
        ]
        selected_vehicles = [
            item for resource_id in assigned_vehicle_ids
            for item in vehicles if item.id == resource_id
        ]
        selected_drivers = [
            item for resource_id in assigned_driver_ids
            for item in drivers if item.id == resource_id
        ]
        vehicle = selected_vehicles[0] if selected_vehicles else None
        driver = selected_drivers[0] if selected_drivers else None
        if vehicle is None:
            missing.append("Выберите активный автобус из профиля")
        if driver is None:
            missing.append("Выберите активного водителя из профиля")

        if trip.trip_date is None:
            missing.append("Дата поездки")
        if not trip.start_time:
            missing.append("Время начала")
        if not trip.end_time:
            missing.append("Время окончания")
        if not trip.route_text:
            missing.append("Подтверждённый маршрут")
        if not trip.customer_type:
            missing.append("Тип заказчика")
        if not trip.customer_name:
            missing.append("Наименование или ФИО заказчика")
        if not trip.customer_address:
            missing.append("Адрес заказчика")
        if trip.customer_type in {"legal", "ip"} and not trip.customer_inn:
            missing.append("ИНН заказчика")

        customer_data = _as_dict(data.get("customer"))
        customer_phone = _required_text(
            customer_data.get("phone"), "Телефон заказчика", missing
        )
        customer_email = _required_text(
            customer_data.get("email"), "Email заказчика", missing
        )

        children: list[ChildDocumentRowDTO] = []
        children_data = _as_list(data.get("children"))
        if not children_data:
            missing.append("Список детей")
        for index, raw in enumerate(children_data, start=1):
            item = _as_dict(raw)
            child_missing: list[str] = []
            full_name = _required_text(
                item.get("full_name"), f"ФИО ребёнка №{index}", child_missing
            )
            birth_date = _parse_date(
                item.get("birth_date"), f"Дата рождения ребёнка №{index}", child_missing
            )
            parent_name = _optional_text(
                item.get("parent_name"), "Законный представитель"
            )
            parent_phone = _required_text(
                item.get("parent_phone"),
                f"Телефон родителя ребёнка №{index}",
                child_missing,
            )
            missing.extend(child_missing)
            if not child_missing and birth_date is not None:
                children.append(
                    ChildDocumentRowDTO(
                        full_name=full_name,
                        birth_date=birth_date,
                        parent_name=parent_name,
                        parent_phone=parent_phone,
                    )
                )

        if trip.passenger_count is None:
            missing.append("Количество детей")
        elif trip.passenger_count != len(children_data):
            missing.append(
                "Количество детей не совпадает с числом строк в списке детей"
            )
        if len(children_data) > 55 and len(selected_vehicles) < 2:
            missing.append(
                "Для группы более 55 детей назначьте несколько автобусов и водителей"
            )

        companions: list[CompanionDocumentRowDTO] = []
        companions_data = _as_list(data.get("companions"))
        if not companions_data:
            missing.append("Список сопровождающих")
        for index, raw in enumerate(companions_data, start=1):
            item = _as_dict(raw)
            companion_missing: list[str] = []
            name = _required_text(
                item.get("full_name"),
                f"ФИО сопровождающего №{index}",
                companion_missing,
            )
            role = _optional_text(item.get("role"), "сопровождающий")
            phone = _required_text(
                item.get("phone"),
                f"Телефон сопровождающего №{index}",
                companion_missing,
            )
            missing.extend(companion_missing)
            if not companion_missing:
                companions.append(
                    CompanionDocumentRowDTO(name=name, role=role, phone=phone)
                )

        region_codes = [
            str(item)
            for item in _as_list(route_data.get("region_codes"))
            if isinstance(item, (str, int)) and str(item).strip()
        ]
        if not region_codes:
            missing.append("Регионы маршрута")
        address_start = _required_text(
            route_data.get("address_start"), "Адрес отправления", missing
        )
        destination = _required_text(
            route_data.get("destination"), "Пункт назначения", missing
        )
        district = _required_text(route_data.get("district"), "Район маршрута", missing)
        roads = _required_text(route_data.get("roads"), "Автомобильные дороги", missing)
        distance = _required_text(
            route_data.get("distance"), "Расстояние маршрута", missing
        )
        duration = _required_text(
            route_data.get("duration"), "Продолжительность маршрута", missing
        )
        stops: list[RouteStopDTO] = []
        raw_stops = _as_list(route_data.get("stops"))
        if not raw_stops and destination:
            raw_stops = [
                {
                    "address": destination,
                    "time": "00:15",
                    "goal": "Окончание поездки",
                }
            ]
        standard_times = {
            "Ожидание": "02:00",
            "Посадка/Высадка детей": "00:15",
            "Окончание поездки": "00:15",
        }
        for index, raw in enumerate(raw_stops, start=1):
            item = _as_dict(raw)
            stop_missing: list[str] = []
            address = _required_text(
                item.get("address"), f"Адрес остановки №{index}", stop_missing
            )
            goal = _required_text(
                item.get("goal"), f"Цель остановки №{index}", stop_missing
            )
            stop_time = standard_times.get(goal) or _required_text(
                item.get("time"), f"Время остановки №{index}", stop_missing
            )
            missing.extend(stop_missing)
            if not stop_missing:
                try:
                    stops.append(
                        RouteStopDTO(
                            address=address,
                            time=stop_time,
                            goal=goal,
                        )
                    )
                except ValidationError:
                    missing.append(f"Проверьте время остановки №{index}")

        if len(selected_vehicles) != len(selected_drivers):
            missing.append("Количество автобусов и водителей должно совпадать")
        if sum(item.seats for item in selected_vehicles) < (
            len(children_data) + len(companions_data)
        ):
            missing.append("В выбранных автобусах недостаточно пассажирских мест")
        for selected_vehicle in selected_vehicles:
            if not selected_vehicle.diagnostic_card_number:
                missing.append(f"Номер диагностической карты {selected_vehicle.registration_plate}")
            if not selected_vehicle.diagnostic_card_valid_until:
                missing.append(f"Срок диагностической карты {selected_vehicle.registration_plate}")
            elif trip.trip_date and trip.trip_date >= selected_vehicle.diagnostic_card_valid_until:
                missing.append(f"Диагностическая карта {selected_vehicle.registration_plate} недействительна")
        for selected_driver in selected_drivers:
            if not selected_driver.birth_date:
                missing.append(f"Дата рождения водителя {selected_driver.full_name}")
            if not selected_driver.license_issued_at:
                missing.append(f"Дата выдачи удостоверения {selected_driver.full_name}")

        if missing:
            raise TripAutomationValidationError(missing)
        assert trip.trip_date and trip.start_time and trip.end_time
        assert trip.customer_type and trip.customer_name and trip.customer_address
        assert trip.passenger_count and carrier and vehicle and driver and signature
        assert vehicle.diagnostic_card_number and vehicle.diagnostic_card_valid_until
        assert driver.license_issued_at

        date_start = datetime.combine(
            trip.trip_date,
            datetime.strptime(trip.start_time, "%H:%M").time(),
        )
        date_end = datetime.combine(
            trip.trip_date,
            datetime.strptime(trip.end_time, "%H:%M").time(),
        )
        if date_end <= date_start:
            raise TripAutomationValidationError(
                ["Время окончания должно быть позже времени начала"]
            )

        party_type = PartyType(trip.customer_type)
        customer = PartyDTO(
            type=party_type,
            entity_name=trip.customer_name if party_type is not PartyType.PHYSICAL else None,
            entity_address=(
                trip.customer_address if party_type is not PartyType.PHYSICAL else None
            ),
            entity_location=(
                trip.customer_address if party_type is not PartyType.PHYSICAL else None
            ),
            name=trip.customer_name if party_type is PartyType.PHYSICAL else None,
            address=trip.customer_address if party_type is PartyType.PHYSICAL else None,
            phone=customer_phone,
            email=customer_email,
            inn=trip.customer_inn if party_type is not PartyType.PHYSICAL else None,
        )
        carrier_party = PartyDTO(
            type=PartyType.LEGAL,
            entity_name=carrier.legal_name,
            entity_address=carrier.legal_address,
            entity_location=carrier.actual_address or carrier.legal_address,
            phone=carrier.phone,
            email=carrier.email,
            inn=carrier.inn,
        )
        carrier_dto = CarrierProfileDTO(
            tenant_id=trip.tenant_id,
            carrier_key="main_carrier",
            party=carrier_party,
            applicant=ApplicantDTO(
                name=carrier.director_full_name,
                position=carrier.director_position,
                phone=carrier.phone,
                email=carrier.email,
            ),
            template_artifact_id="builtin:base_notification_unsigned",
            signature_artifact_id=signature.id,
            seal_artifact_id=assets.get("stamp").id if assets.get("stamp") else None,
        )
        vehicle_dtos = [VehicleProfileDTO(id=item.id, brand_and_model=item.make_model, number=item.registration_plate, diagnostic_card_number=item.diagnostic_card_number, diagnostic_card_valid_until=item.diagnostic_card_valid_until, active=item.is_active) for item in selected_vehicles]
        driver_dtos = [DriverProfileDTO(id=item.id, name=item.full_name, licence=item.license_number, licence_issue_date=item.license_issued_at, birth_date=item.birth_date, active=item.is_active) for item in selected_drivers]
        vehicle_dto = vehicle_dtos[0]
        driver_dto = driver_dtos[0]
        route = RouteDTO(
            address_start=address_start,
            address_start_map_query=address_start,
            route_region_code="78" if "78" in region_codes else region_codes[0],
            route_region_name=(
                "г. Санкт-Петербург" if "78" in region_codes else None
            ),
            district=district,
            roads=roads,
            stop_points=stops,
            distance=distance,
            duration=duration,
            use_map=False,
        )
        return PreparedTrip(
            trip=trip,
            carrier=carrier,
            carrier_dto=carrier_dto,
            vehicle=vehicle,
            vehicle_dto=vehicle_dto,
            driver=driver,
            driver_dto=driver_dto,
            vehicles=selected_vehicles,
            vehicle_dtos=vehicle_dtos,
            drivers=selected_drivers,
            driver_dtos=driver_dtos,
            customer=customer,
            transportation=TransportationInputDTO(
                region_codes=region_codes,
                children_count=trip.passenger_count,
                date_start=date_start,
                date_end=date_end,
            ),
            route=route,
            children=children,
            companions=companions,
            signature_path=resolve_storage_path(
                signature.storage_key, settings=self.settings
            ),
            stamp_path=(
                resolve_storage_path(
                    assets["stamp"].storage_key, settings=self.settings
                )
                if "stamp" in assets
                else None
            ),
            signature_file_id=signature.id,
        )

    async def run(
        self,
        db: AsyncSession,
        *,
        job_id: str,
        portal_adapter: GibddPortalAdapter | None = None,
    ) -> SubmissionJob:
        job = await db.scalar(
            select(SubmissionJob).where(SubmissionJob.id == job_id)
        )
        if job is None:
            raise AutomationError("Submission job not found")
        trip = await db.scalar(
            select(Trip).where(
                Trip.id == job.trip_id,
                Trip.tenant_id == job.tenant_id,
            )
        )
        if trip is None:
            raise AutomationError("Trip not found for submission job")
        if job.status in {"dry_run_ready", "submitted"}:
            return job
        if trip.status not in {
            "confirmed",
            "generating_documents",
            "building_payload",
            "ready_to_submit",
            "submitting",
            "failed",
            "retry_scheduled",
        }:
            raise AutomationError("Trip is not confirmed")

        attempt: SubmissionAttempt | None = None
        attempt_id: str | None = None
        prepared: PreparedTrip | None = None
        try:
            prepared = await self.prepare_confirmation(db, trip)
            trip.failure_reason = None
            trip.status = "generating_documents"
            trip.progress = 50
            job.status = "generating_documents"
            job.started_at = job.started_at or utcnow()
            self._audit(db, trip, "trip.documents_generation_started", "trip")
            await db.commit()

            docx_file, pdf_file = await self._documents(db, job, prepared)
            job.notification_docx_file_id = docx_file.id
            job.notification_pdf_file_id = pdf_file.id
            trip.status = "building_payload"
            trip.progress = 60
            job.status = "building_payload"
            await db.commit()

            payload_row, payload = await self._payload(
                db,
                job,
                prepared,
                pdf_file,
            )
            job.payload_id = payload_row.id
            trip.status = "ready_to_submit"
            trip.progress = 70
            job.status = "ready_to_submit"
            await db.commit()

            job.attempt_count += 1
            attempt = SubmissionAttempt(
                tenant_id=trip.tenant_id,
                job_id=job.id,
                attempt_number=job.attempt_count,
                status="submitting",
            )
            db.add(attempt)
            trip.status = "submitting"
            trip.progress = 75
            job.status = "submitting"
            self._message(
                db,
                trip,
                "Документы сформированы. Безопасно заполняю форму ГИБДД "
                "до финального подтверждения.",
                message_type="system_status",
            )
            self._audit(db, trip, "submission.attempt_started", "submission_job", job.id)
            await db.commit()
            await db.refresh(attempt)
            attempt_id = attempt.id

            diagnostics_dir = self._diagnostics_dir(trip, job, attempt)
            worker = self.submission_worker or SubmissionWorker(
                config=SubmissionWorkerConfig(
                    base_url=self.settings.gibdd_base_url,
                    live_submission=self.settings.gibdd_live_submission,
                    headful=self.settings.gibdd_headful,
                    route_direct=self.settings.gibdd_route_direct,
                    keep_browser_open_on_dry_run=(
                        self.settings.gibdd_headful
                        and self.settings.app_env.lower() != "production"
                    ),
                    screenshot_dir=diagnostics_dir,
                )
            )
            outcome = await worker.run(
                payload,
                file_resolver=RootStorageFileResolver(self.settings.storage_root),
                adapter=portal_adapter,
            )
            diagnostics = await self._persist_diagnostics(
                db,
                trip,
                diagnostics_dir,
                extra_paths=outcome.screenshots,
            )
            attempt.status = outcome.status.value
            attempt.final_submit_attempted = outcome.final_submit_attempted
            attempt.current_url = outcome.current_url
            attempt.diagnostic_file_ids = [item.id for item in diagnostics]
            attempt.completed_at = utcnow()
            job.status = outcome.status.value
            job.final_submit_attempted = outcome.final_submit_attempted
            job.portal_url = outcome.current_url
            job.portal_token = outcome.token
            job.completed_at = utcnow()
            trip.status = outcome.status.value
            trip.progress = (
                90
                if outcome.status
                in {SubmissionStatus.DRY_RUN_READY, SubmissionStatus.SUBMITTED}
                else 75
            )
            if outcome.token:
                trip.portal_token = outcome.token
                trip.submitted_at = utcnow()
            if outcome.status is SubmissionStatus.DRY_RUN_READY:
                await settle_reservation(
                    db,
                    tenant_id=trip.tenant_id,
                    actor_id=trip.created_by_id,
                    trip_id=trip.id,
                    success=False,
                    idempotency_key=f"trip:{trip.id}:release:dry-run",
                )
                content = (
                    "Безопасное заполнение завершено: форма ГИБДД доведена "
                    "до финального подтверждения, отправка не выполнялась. "
                    "DOCX и PDF сохранены в уведомлениях."
                )
            elif outcome.status is SubmissionStatus.SUBMITTED:
                await settle_reservation(
                    db,
                    tenant_id=trip.tenant_id,
                    actor_id=trip.created_by_id,
                    trip_id=trip.id,
                    success=True,
                    idempotency_key=f"trip:{trip.id}:capture",
                )
                content = "Уведомление отправлено. Готовые документы сохранены."
            else:
                content = outcome.message or "Подача остановлена для проверки."
            self._message(
                db,
                trip,
                content,
                attachments=[docx_file.id, pdf_file.id],
                message_type="artifact_message",
                metadata={
                    "status": outcome.status.value,
                    "job_id": job.id,
                    "final_submit_attempted": outcome.final_submit_attempted,
                },
            )
            self._audit(
                db,
                trip,
                "submission.dry_run_ready"
                if outcome.status is SubmissionStatus.DRY_RUN_READY
                else "submission.finished",
                "submission_job",
                job.id,
                {
                    "status": outcome.status.value,
                    "final_submit_attempted": outcome.final_submit_attempted,
                    "diagnostic_file_ids": attempt.diagnostic_file_ids,
                },
            )
            await db.commit()
            await db.refresh(job)
            return job
        except Exception as exc:
            await db.rollback()
            job = await db.scalar(
                select(SubmissionJob).where(SubmissionJob.id == job_id)
            )
            trip = await db.scalar(select(Trip).where(Trip.id == job.trip_id)) if job else None
            if job is not None and trip is not None:
                message = str(exc)[:4000] or type(exc).__name__
                job.status = "failed"
                job.last_error = message
                job.completed_at = utcnow()
                trip.status = "failed"
                trip.failure_reason = message
                if attempt_id is not None:
                    persisted_attempt = await db.get(SubmissionAttempt, attempt_id)
                    if persisted_attempt is not None:
                        persisted_attempt.status = "failed"
                        persisted_attempt.error_message = message
                        persisted_attempt.completed_at = utcnow()
                if not job.final_submit_attempted:
                    try:
                        await settle_reservation(
                            db,
                            tenant_id=trip.tenant_id,
                            actor_id=trip.created_by_id,
                            trip_id=trip.id,
                            success=False,
                            idempotency_key=f"trip:{trip.id}:release:failed",
                        )
                    except Exception:
                        # A reservation may not exist when validation was
                        # invoked directly by an administrator/test.
                        await db.rollback()
                        job = await db.get(SubmissionJob, job_id)
                        trip = await db.get(Trip, job.trip_id) if job else None
                        if job is None or trip is None:
                            raise
                        job.status = "failed"
                        job.last_error = message
                        job.completed_at = utcnow()
                        trip.status = "failed"
                        trip.failure_reason = message
                self._message(
                    db,
                    trip,
                    "Не удалось продолжить автоматическую обработку: " + message,
                    message_type="system_status",
                    metadata={"status": "failed", "job_id": job.id},
                )
                self._audit(
                    db,
                    trip,
                    "submission.failed",
                    "submission_job",
                    job.id,
                    {"error": message},
                )
                await db.commit()
            raise

    async def _documents(
        self,
        db: AsyncSession,
        job: SubmissionJob,
        prepared: PreparedTrip,
    ) -> tuple[StoredFile, StoredFile]:
        existing_docx = (
            await db.get(StoredFile, job.notification_docx_file_id)
            if job.notification_docx_file_id
            else None
        )
        existing_pdf = (
            await db.get(StoredFile, job.notification_pdf_file_id)
            if job.notification_pdf_file_id
            else None
        )
        if existing_docx and existing_pdf:
            docx_path = resolve_storage_path(
                existing_docx.storage_key, settings=self.settings
            )
            pdf_path = resolve_storage_path(
                existing_pdf.storage_key, settings=self.settings
            )
            if docx_path.is_file() and pdf_path.read_bytes()[:4] == b"%PDF":
                return existing_docx, existing_pdf

        trip = prepared.trip
        base_key = (
            f"{trip.tenant_id}/{trip.trip_date:%Y/%m}/notifications/"
            f"{trip.id}/{uuid.uuid4().hex}"
        )
        docx_key = f"{base_key}.docx"
        pdf_key = f"{base_key}.pdf"
        docx_path = resolve_storage_path(docx_key, settings=self.settings)
        pdf_path = resolve_storage_path(pdf_key, settings=self.settings)
        docx_path.parent.mkdir(parents=True, exist_ok=True)

        customer = _as_dict(trip.extracted_data.get("customer"))
        route_data = _as_dict(trip.extracted_data.get("route"))
        destination = str(route_data["destination"]).strip()
        stops_text = "; ".join(
            str(_as_dict(item).get("address", "")).strip()
            for item in _as_list(route_data.get("stops"))
            if str(_as_dict(item).get("address", "")).strip() != destination
        )
        trip_points = (
            f"{trip.trip_date:%d.%m.%Y}, {trip.start_time}–{trip.end_time}. "
            f"{trip.route_text}"
        )
        replacements = {
            "{{ORGANIZER_NAME}}": trip.customer_name,
            "{{ORGANIZER_ADDRESS}}": trip.customer_address,
            "{{ORGANIZER_PHONE}}": str(customer["phone"]).strip(),
            "{{ORGANIZER_EMAIL}}": str(customer["email"]).strip(),
            "{{ORGANIZER_INN}}": trip.customer_inn or "не применяется",
            "{{CARRIER_NAME}}": prepared.carrier.legal_name,
            "{{CARRIER_ADDRESS}}": prepared.carrier.legal_address,
            "{{CARRIER_PHONE}}": prepared.carrier.phone,
            "{{CARRIER_EMAIL}}": prepared.carrier.email,
            "{{CARRIER_INN}}": prepared.carrier.inn,
            "{{CARRIER_LICENSE}}": prepared.carrier.license_number or "не применяется",
            "{{TRIP_DATETIME_AND_POINTS}}": trip_points,
            "{{INTERMEDIATE_STOPS}}": stops_text or "-",
            "{{DESTINATION_POINT}}": destination,
            "{{SIGNATORY_POSITION}}": prepared.carrier.director_position,
            "{{SIGNATORY_NAME}}": prepared.carrier.director_full_name,
        }
        route_lines = [
            line.strip()
            for line in str(trip.route_text).splitlines()
            if line.strip()
        ]
        self.documents.generate_docx(
            DocumentGenerationRequestDTO(
                tenant_id=trip.tenant_id,
                template_path=self.settings.notification_template_path,
                target_path=docx_path,
                trip_date=trip.trip_date,
                replacements=replacements,
                children=prepared.children,
                companions=prepared.companions,
                buses=[
                    BusDocumentRowDTO(
                        model=vehicle.make_model,
                        number=vehicle.registration_plate,
                        diagnostic_card_info=_diagnostic_card_text(vehicle),
                    )
                    for vehicle in prepared.vehicles
                ],
                drivers=[
                    DriverDocumentRowDTO(
                        name=driver.full_name,
                        birth_date=driver.birth_date,
                        licence=driver.license_number,
                        licence_issue_date=driver.license_issued_at,
                    )
                    for driver in prepared.drivers
                ],
                route_lines=route_lines,
                signature_path=prepared.signature_path,
                stamp_path=prepared.stamp_path,
            )
        )
        self.documents.convert_to_pdf(docx_path, target_path=pdf_path)
        docx_file = self._stored_artifact(
            trip,
            storage_key=docx_key,
            original_name=f"{trip.reference} {trip.title}.docx",
            content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            kind="notification",
        )
        pdf_file = self._stored_artifact(
            trip,
            storage_key=pdf_key,
            original_name=f"{trip.reference} {trip.title}.pdf",
            content_type="application/pdf",
            kind="notification",
        )
        db.add_all([docx_file, pdf_file])
        await db.flush()
        self._audit(
            db,
            trip,
            "trip.documents_generated",
            "trip",
            trip.id,
            {"docx_file_id": docx_file.id, "pdf_file_id": pdf_file.id},
        )
        return docx_file, pdf_file

    async def _payload(
        self,
        db: AsyncSession,
        job: SubmissionJob,
        prepared: PreparedTrip,
        pdf_file: StoredFile,
    ) -> tuple[TripPayload, GibddPayloadDTO]:
        if job.payload_id:
            existing = await db.get(TripPayload, job.payload_id)
            if existing is not None:
                return existing, self.payloads.validate(existing.payload_json)
        payload = self.payloads.build(
            PayloadBuildInputDTO(
                tenant_id=prepared.trip.tenant_id,
                carrier_profile=prepared.carrier_dto,
                transportation=prepared.transportation,
                customer=prepared.customer,
                vehicles=prepared.vehicle_dtos,
                drivers=prepared.driver_dtos,
                route=prepared.route,
                files=[pdf_file.storage_key],
            )
        )
        raw = payload.model_dump(mode="json")
        canonical = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        row = TripPayload(
            tenant_id=prepared.trip.tenant_id,
            trip_id=prepared.trip.id,
            revision=1,
            status="validated",
            payload_json=raw,
            checksum_sha256=hashlib.sha256(canonical).hexdigest(),
        )
        db.add(row)
        await db.flush()
        self._audit(
            db,
            prepared.trip,
            "trip.payload_validated",
            "trip_payload",
            row.id,
            {"checksum_sha256": row.checksum_sha256},
        )
        return row, payload

    async def _persist_diagnostics(
        self,
        db: AsyncSession,
        trip: Trip,
        directory: Path,
        *,
        extra_paths: list[Path] | None = None,
    ) -> list[StoredFile]:
        sources = list(directory.iterdir()) if directory.exists() else []
        for extra in extra_paths or []:
            sources.append(extra)
            html_sidecar = extra.with_suffix(".html")
            if html_sidecar.is_file():
                sources.append(html_sidecar)
        unique_sources = {
            source.resolve(): source
            for source in sources
            if source.is_file()
        }
        artifacts: list[StoredFile] = []
        for source in sorted(unique_sources.values(), key=lambda item: str(item)):
            try:
                source.relative_to(self.settings.storage_root.resolve())
                target = source
            except ValueError:
                key = (
                    f"{trip.tenant_id}/{trip.trip_date:%Y/%m}/diagnostics/"
                    f"{trip.id}/{uuid.uuid4().hex}{source.suffix.lower()}"
                )
                target = resolve_storage_path(key, settings=self.settings)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            key = str(target.relative_to(self.settings.storage_root.resolve()))
            content_type = {
                ".png": "image/png",
                ".html": "text/html; charset=utf-8",
            }.get(target.suffix.lower(), "application/octet-stream")
            artifact = self._stored_artifact(
                trip,
                storage_key=key,
                original_name=source.name,
                content_type=content_type,
                kind="diagnostic",
            )
            db.add(artifact)
            artifacts.append(artifact)
        if artifacts:
            await db.flush()
        return artifacts

    def _stored_artifact(
        self,
        trip: Trip,
        *,
        storage_key: str,
        original_name: str,
        content_type: str,
        kind: str,
    ) -> StoredFile:
        path = resolve_storage_path(storage_key, settings=self.settings)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return StoredFile(
            tenant_id=trip.tenant_id,
            uploaded_by_id=trip.created_by_id,
            conversation_id=trip.conversation_id,
            trip_id=trip.id,
            kind=kind,
            original_name=original_name[:512],
            storage_key=storage_key,
            content_type=content_type,
            size=path.stat().st_size,
            checksum_sha256=digest,
        )

    def _diagnostics_dir(
        self,
        trip: Trip,
        job: SubmissionJob,
        attempt: SubmissionAttempt,
    ) -> Path:
        return resolve_storage_path(
            (
                f"{trip.tenant_id}/{trip.trip_date:%Y/%m}/diagnostics/"
                f"{job.id}/{attempt.attempt_number}"
            ),
            settings=self.settings,
        )

    @staticmethod
    def _message(
        db: AsyncSession,
        trip: Trip,
        content: str,
        *,
        attachments: list[str] | None = None,
        message_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not trip.conversation_id:
            return
        db.add(
            Message(
                tenant_id=trip.tenant_id,
                conversation_id=trip.conversation_id,
                role="assistant",
                content=content,
                attachments=attachments or [],
                metadata_json={
                    "message_type": message_type,
                    **(metadata or {}),
                },
            )
        )

    @staticmethod
    def _audit(
        db: AsyncSession,
        trip: Trip,
        action: str,
        entity_type: str,
        entity_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        db.add(
            AuditEvent(
                tenant_id=trip.tenant_id,
                actor_id=trip.created_by_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details or {},
            )
        )
