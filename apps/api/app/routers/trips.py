from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit import add_audit_event
from ..dependencies import CurrentUser, DBSession, SystemOperator
from ..automation.orchestration import (
    ConfirmedTripOrchestrator,
    TripAutomationValidationError,
    ensure_submission_job,
)
from ..automation.workflow import apply_manual_trip_updates
from ..models import (
    Conversation,
    Driver,
    Message,
    StoredFile,
    SubmissionAttempt,
    SubmissionJob,
    Trip,
    Vehicle,
)
from ..schemas import (
    SubmissionAttemptRead,
    FileRead,
    TripAutomationRead,
    TripCreate,
    TripProgressUpdate,
    TripRead,
    TripUpdate,
)
from ..trip_references import next_trip_reference
from ..worker import celery_app
from .billing import reserve_notification
from .onboarding import build_onboarding_status

router = APIRouter(prefix="/trips", tags=["trips"])


async def _run_local_submission_job(app, job_id: str) -> None:
    """Zero-setup development fallback when Redis is not running."""

    async with app.state.session_factory() as db:
        try:
            await ConfirmedTripOrchestrator(settings=app.state.settings).run(
                db,
                job_id=job_id,
            )
        except Exception:
            # The orchestrator persists the failed state, releases an unused
            # reservation and records a user-visible message.  A local
            # BackgroundTask must not turn that handled worker failure into an
            # unhandled ASGI request error.
            return


async def _trip(db, trip_id: str, tenant_id: str) -> Trip:
    trip = (
        await db.execute(
            select(Trip).where(Trip.id == trip_id, Trip.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if trip is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return trip


async def _validate_relations(db, tenant_id: str, values: dict) -> None:
    checks = (
        ("conversation_id", Conversation, "Диалог"),
        ("selected_vehicle_id", Vehicle, "Автобус"),
        ("selected_driver_id", Driver, "Водитель"),
    )
    for field, model, label in checks:
        entity_id = values.get(field)
        if entity_id:
            exists = await db.scalar(
                select(model.id).where(model.id == entity_id, model.tenant_id == tenant_id)
            )
            if exists is None:
                raise HTTPException(status_code=422, detail=f"{label} недоступен")


@router.get("", response_model=list[TripRead])
async def list_trips(
    user: CurrentUser,
    db: DBSession,
    status: str | None = None,
    limit: int = 100,
) -> list[Trip]:
    query = select(Trip).where(Trip.tenant_id == user.tenant_id)
    if status:
        query = query.where(Trip.status == status)
    query = query.order_by(Trip.updated_at.desc()).limit(min(limit, 200))
    return list((await db.scalars(query)).all())


@router.post("", response_model=TripRead, status_code=201)
async def create_trip(
    payload: TripCreate,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> Trip:
    values = payload.model_dump()
    await _validate_relations(db, user.tenant_id, values)
    conversation_id = values.get("conversation_id")
    if conversation_id and await db.scalar(
        select(Trip.id).where(
            Trip.tenant_id == user.tenant_id,
            Trip.conversation_id == conversation_id,
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="Для диалога уже создана заявка",
        )
    trip = Trip(
        tenant_id=user.tenant_id,
        created_by_id=user.id,
        reference=await next_trip_reference(db),
        **values,
    )
    db.add(trip)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Для диалога уже создана заявка",
        ) from exc
    add_audit_event(
        db,
        user=user,
        action="trip.created",
        entity_type="trip",
        entity_id=trip.id,
        request=request,
    )
    await db.commit()
    await db.refresh(trip)
    return trip


@router.get("/{trip_id}", response_model=TripRead)
async def get_trip(trip_id: str, user: CurrentUser, db: DBSession) -> Trip:
    return await _trip(db, trip_id, user.tenant_id)


@router.get("/{trip_id}/automation", response_model=TripAutomationRead)
async def get_trip_automation(
    trip_id: str,
    user: CurrentUser,
    db: DBSession,
) -> TripAutomationRead:
    trip = await _trip(db, trip_id, user.tenant_id)
    job = await db.scalar(
        select(SubmissionJob).where(
            SubmissionJob.trip_id == trip.id,
            SubmissionJob.tenant_id == user.tenant_id,
        )
    )
    if job is None:
        return TripAutomationRead(trip_id=trip.id, status=trip.status)
    attempts = list(
        (
            await db.scalars(
                select(SubmissionAttempt)
                .where(
                    SubmissionAttempt.job_id == job.id,
                    SubmissionAttempt.tenant_id == user.tenant_id,
                )
                .order_by(SubmissionAttempt.attempt_number)
            )
        ).all()
    )
    diagnostic_ids = {
        file_id
        for attempt in attempts
        for file_id in attempt.diagnostic_file_ids
    }
    diagnostics = (
        list(
            (
                await db.scalars(
                    select(StoredFile)
                    .where(
                        StoredFile.id.in_(diagnostic_ids),
                        StoredFile.tenant_id == user.tenant_id,
                        StoredFile.trip_id == trip.id,
                        StoredFile.kind == "diagnostic",
                        StoredFile.is_deleted.is_(False),
                    )
                    .order_by(StoredFile.created_at)
                )
            ).all()
        )
        if diagnostic_ids
        else []
    )
    return TripAutomationRead(
        trip_id=trip.id,
        job_id=job.id,
        status=job.status,
        payload_id=job.payload_id,
        notification_docx_file_id=job.notification_docx_file_id,
        notification_pdf_file_id=job.notification_pdf_file_id,
        live_submission=job.live_submission,
        attempt_count=job.attempt_count,
        final_submit_attempted=job.final_submit_attempted,
        portal_url=job.portal_url,
        portal_token=job.portal_token,
        last_error=job.last_error,
        attempts=[SubmissionAttemptRead.model_validate(item) for item in attempts],
        diagnostic_files=[
            FileRead.model_validate(item).model_copy(
                update={"trip_reference": trip.reference}
            )
            for item in diagnostics
        ],
    )


@router.patch("/{trip_id}", response_model=TripRead)
async def update_trip(
    trip_id: str,
    payload: TripUpdate,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> Trip:
    trip = await _trip(db, trip_id, user.tenant_id)
    if trip.status in {"submitting", "submitted"}:
        raise HTTPException(status_code=409, detail="Поданная заявка недоступна для изменения")
    values = payload.model_dump(exclude_unset=True)
    await _validate_relations(db, user.tenant_id, values)
    for field, value in values.items():
        setattr(trip, field, value)
    apply_manual_trip_updates(trip, values)
    add_audit_event(
        db,
        user=user,
        action="trip.updated",
        entity_type="trip",
        entity_id=trip.id,
        request=request,
    )
    await db.commit()
    await db.refresh(trip)
    return trip


@router.patch("/{trip_id}/progress", response_model=TripRead)
async def update_progress(
    trip_id: str,
    payload: TripProgressUpdate,
    request: Request,
    user: SystemOperator,
    db: DBSession,
) -> Trip:
    trip = await _trip(db, trip_id, user.tenant_id)
    trip.status = payload.status
    trip.progress = payload.progress
    add_audit_event(
        db,
        user=user,
        action="trip.progress_changed",
        entity_type="trip",
        entity_id=trip.id,
        details={"status": payload.status, "progress": payload.progress},
        request=request,
    )
    await db.commit()
    await db.refresh(trip)
    return trip


@router.post("/{trip_id}/confirm", response_model=TripRead)
async def confirm_trip(
    trip_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> Trip:
    trip = await _trip(db, trip_id, user.tenant_id)
    existing_job = await db.scalar(
        select(SubmissionJob).where(
            SubmissionJob.trip_id == trip.id,
            SubmissionJob.tenant_id == user.tenant_id,
        )
    )
    if existing_job and existing_job.status in {"dry_run_ready", "submitted"}:
        return trip
    onboarding = await build_onboarding_status(db, user.tenant_id)
    if not onboarding.completed:
        raise HTTPException(
            status_code=409,
            detail="Сначала завершите профиль перевозчика",
        )
    orchestrator = ConfirmedTripOrchestrator(settings=request.app.state.settings)
    try:
        await orchestrator.prepare_confirmation(db, trip)
    except TripAutomationValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Перед подтверждением заполните обязательные данные",
                "missing_fields": exc.missing,
            },
        ) from exc

    await reserve_notification(
        db,
        tenant_id=user.tenant_id,
        actor_id=user.id,
        trip_id=trip.id,
        idempotency_key=f"trip:{trip.id}:reserve",
    )
    trip.status = "confirmed"
    trip.progress = max(trip.progress, 35)
    trip.failure_reason = None
    trip.confirmed_at = datetime.now(timezone.utc)
    job = await ensure_submission_job(
        db,
        trip=trip,
        live_submission=request.app.state.settings.gibdd_live_submission,
    )
    if trip.conversation_id:
        db.add(
            Message(
                tenant_id=trip.tenant_id,
                conversation_id=trip.conversation_id,
                role="assistant",
                content=(
                    "Данные подтверждены. Запускаю формирование уведомления "
                    "и безопасное заполнение сайта ГИБДД."
                ),
                metadata_json={
                    "message_type": "system_status",
                    "status": "confirmed",
                    "job_id": job.id,
                },
            )
        )
    add_audit_event(
        db,
        user=user,
        action="trip.confirmed",
        entity_type="trip",
        entity_id=trip.id,
        details={"submission_job_id": job.id},
        request=request,
    )
    await db.commit()
    await db.refresh(trip)
    if request.app.state.settings.app_env.lower() != "test":
        try:
            celery_app.send_task(
                "automation.process_confirmed_trip",
                args=[job.id],
                queue="browser",
            )
        except Exception as exc:
            if request.app.state.settings.app_env.lower() in {
                "development",
                "local",
            }:
                background_tasks.add_task(
                    _run_local_submission_job,
                    request.app,
                    job.id,
                )
                return trip
            async with request.app.state.session_factory() as recovery_db:
                recovery_job = await recovery_db.get(SubmissionJob, job.id)
                if recovery_job is not None:
                    recovery_job.status = "retry_scheduled"
                    recovery_job.last_error = "Очередь browser-worker недоступна"
                    await recovery_db.commit()
            raise HTTPException(
                status_code=503,
                detail=(
                    "Данные подтверждены, но browser-worker временно недоступен. "
                    "Повторите запуск после восстановления очереди."
                ),
            ) from exc
    return trip
