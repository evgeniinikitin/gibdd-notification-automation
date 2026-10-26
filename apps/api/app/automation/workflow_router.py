"""Authenticated API contract for persisted chat extraction state."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select

from ..dependencies import CurrentUser, DBSession
from ..models import Conversation, Message, Trip
from .workflow import (
    dispatch_trip_workflow,
    latest_workflow_run,
    prepare_workflow_run,
    workflow_state_payload,
)


router = APIRouter(tags=["assistant workflow"])


async def _conversation_trip(
    db,
    *,
    conversation_id: str,
    tenant_id: str,
) -> tuple[Conversation, Trip]:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    trip = await db.scalar(
        select(Trip).where(
            Trip.conversation_id == conversation.id,
            Trip.tenant_id == tenant_id,
        )
    )
    if trip is None:
        raise HTTPException(
            status_code=404,
            detail="В диалоге ещё нет заявки",
        )
    return conversation, trip


async def _tenant_trip(db, *, trip_id: str, tenant_id: str) -> Trip:
    trip = await db.scalar(
        select(Trip).where(
            Trip.id == trip_id,
            Trip.tenant_id == tenant_id,
        )
    )
    if trip is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return trip


async def _latest_user_message(db, trip: Trip) -> Message:
    if trip.conversation_id is None:
        raise HTTPException(
            status_code=409,
            detail="Для автоматического разбора заявка должна быть связана с диалогом",
        )
    message = await db.scalar(
        select(Message)
        .where(
            Message.tenant_id == trip.tenant_id,
            Message.conversation_id == trip.conversation_id,
            Message.role == "user",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message is None:
        raise HTTPException(
            status_code=409,
            detail="Сначала отправьте сообщение или вложение в диалог",
        )
    return message


async def _retry(
    *,
    trip: Trip,
    request: Request,
    background_tasks: BackgroundTasks,
    user,
    db,
) -> dict:
    message = await _latest_user_message(db, trip)
    run, should_run = await prepare_workflow_run(
        db,
        app=request.app,
        trip=trip,
        message=message,
        user=user,
        request=request,
    )
    await db.commit()
    await db.refresh(run)
    await db.refresh(trip)
    if should_run:
        dispatch_trip_workflow(
            request.app,
            run.id,
            background_tasks=background_tasks,
        )
    return workflow_state_payload(trip=trip, run=run)


@router.get("/conversations/{conversation_id}/workflow")
async def get_conversation_workflow(
    conversation_id: str,
    user: CurrentUser,
    db: DBSession,
) -> dict:
    _, trip = await _conversation_trip(
        db,
        conversation_id=conversation_id,
        tenant_id=user.tenant_id,
    )
    run = await latest_workflow_run(
        db, trip_id=trip.id, tenant_id=user.tenant_id
    )
    return workflow_state_payload(trip=trip, run=run)


@router.post("/conversations/{conversation_id}/workflow/retry")
async def retry_conversation_workflow(
    conversation_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: DBSession,
) -> dict:
    _, trip = await _conversation_trip(
        db,
        conversation_id=conversation_id,
        tenant_id=user.tenant_id,
    )
    return await _retry(
        trip=trip,
        request=request,
        background_tasks=background_tasks,
        user=user,
        db=db,
    )


@router.get("/trips/{trip_id}/processing")
async def get_trip_processing(
    trip_id: str,
    user: CurrentUser,
    db: DBSession,
) -> dict:
    trip = await _tenant_trip(db, trip_id=trip_id, tenant_id=user.tenant_id)
    run = await latest_workflow_run(
        db, trip_id=trip.id, tenant_id=user.tenant_id
    )
    return workflow_state_payload(trip=trip, run=run)


@router.post("/trips/{trip_id}/extract")
async def extract_trip(
    trip_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: DBSession,
) -> dict:
    trip = await _tenant_trip(db, trip_id=trip_id, tenant_id=user.tenant_id)
    return await _retry(
        trip=trip,
        request=request,
        background_tasks=background_tasks,
        user=user,
        db=db,
    )
