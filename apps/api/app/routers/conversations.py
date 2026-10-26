from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..audit import add_audit_event
from ..dependencies import CurrentUser, DBSession
from ..models import Conversation, Message, StoredFile, Trip, User, utcnow
from ..schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessageRead,
)
from ..trip_references import next_trip_reference
from ..automation.workflow import (
    dispatch_trip_workflow,
    prepare_workflow_run,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])

GENERIC_CONVERSATION_TITLES = {"", "Новая заявка", "Новая поездка"}
PROFILE_COMMAND_PATTERN = re.compile(
    r"(?iu)\b(автобус\w*|водител\w*|автопарк\w*|госномер\w*|"
    r"удостоверен\w*|обнов\w*\s+профил\w*)\b"
)


async def _conversation(db, conversation_id: str, tenant_id: str, with_messages=False):
    query = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.tenant_id == tenant_id,
    )
    if with_messages:
        query = query.options(selectinload(Conversation.messages))
    conversation = (await db.execute(query)).scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return conversation


def _short_title(value: str, *, max_length: int = 120) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 1].rstrip()}…"


def _trip_title(
    conversation: Conversation,
    content: str,
    attachments: list[StoredFile],
) -> str:
    conversation_title = _short_title(conversation.title)
    if conversation_title not in GENERIC_CONVERSATION_TITLES:
        return conversation_title
    if content:
        return _short_title(content)
    if attachments:
        filename = Path(attachments[0].original_name).stem
        if filename.strip():
            return _short_title(filename)
    return "Новая заявка"


async def _message_attachments(
    db: AsyncSession,
    *,
    attachment_ids: list[str],
    tenant_id: str,
    conversation_id: str,
) -> list[StoredFile]:
    if not attachment_ids:
        return []
    unique_ids = set(attachment_ids)
    files = list(
        (
            await db.scalars(
                select(StoredFile).where(
                    StoredFile.tenant_id == tenant_id,
                    StoredFile.id.in_(unique_ids),
                    StoredFile.is_deleted.is_(False),
                )
            )
        ).all()
    )
    by_id = {stored.id: stored for stored in files}
    if set(by_id) != unique_ids:
        raise HTTPException(status_code=422, detail="Один из файлов недоступен")
    ordered = [by_id[file_id] for file_id in attachment_ids]
    if any(
        stored.conversation_id not in {None, conversation_id}
        for stored in ordered
    ):
        raise HTTPException(
            status_code=422,
            detail="Один из файлов прикреплён к другому диалогу",
        )
    return ordered


async def _ensure_conversation_trip(
    db: AsyncSession,
    *,
    conversation: Conversation,
    user: User,
    content: str,
    attachments: list[StoredFile],
    request: Request,
) -> Trip:
    trip = await db.scalar(
        select(Trip).where(
            Trip.tenant_id == user.tenant_id,
            Trip.conversation_id == conversation.id,
        )
    )
    title = _trip_title(conversation, content, attachments)
    created = False
    if trip is None:
        try:
            async with db.begin_nested():
                candidate = Trip(
                    reference=await next_trip_reference(db),
                    tenant_id=user.tenant_id,
                    conversation_id=conversation.id,
                    created_by_id=user.id,
                    title=title,
                    status="draft",
                    progress=0,
                )
                db.add(candidate)
                await db.flush()
            trip = candidate
            created = True
        except IntegrityError:
            # A concurrent first message may have created the one allowed trip.
            trip = await db.scalar(
                select(Trip).where(
                    Trip.tenant_id == user.tenant_id,
                    Trip.conversation_id == conversation.id,
                )
            )
            if trip is None:
                raise

    now = utcnow()
    trip.updated_at = now
    if trip.title in GENERIC_CONVERSATION_TITLES and title not in GENERIC_CONVERSATION_TITLES:
        trip.title = title
    if conversation.title in GENERIC_CONVERSATION_TITLES and title not in GENERIC_CONVERSATION_TITLES:
        conversation.title = title
    conversation.updated_at = now

    for stored in attachments:
        stored.conversation_id = conversation.id
        if stored.trip_id not in {None, trip.id}:
            raise HTTPException(
                status_code=422,
                detail="Один из файлов уже связан с другой заявкой",
            )
        stored.trip_id = trip.id

    if created:
        add_audit_event(
            db,
            user=user,
            action="trip.created_from_conversation",
            entity_type="trip",
            entity_id=trip.id,
            details={"conversation_id": conversation.id},
            request=request,
        )
    return trip


@router.get("", response_model=list[ConversationRead])
async def list_conversations(
    user: CurrentUser,
    db: DBSession,
    status: str | None = None,
    limit: int = 100,
) -> list[Conversation]:
    query = select(Conversation).where(Conversation.tenant_id == user.tenant_id)
    if status:
        query = query.where(Conversation.status == status)
    query = query.order_by(Conversation.updated_at.desc()).limit(min(limit, 200))
    return list((await db.scalars(query)).all())


@router.post("", response_model=ConversationRead, status_code=201)
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> Conversation:
    conversation = Conversation(
        tenant_id=user.tenant_id,
        created_by_id=user.id,
        title=payload.title,
    )
    db.add(conversation)
    await db.flush()
    add_audit_event(
        db,
        user=user,
        action="conversation.created",
        entity_type="conversation",
        entity_id=conversation.id,
        request=request,
    )
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str, user: CurrentUser, db: DBSession
) -> Conversation:
    return await _conversation(db, conversation_id, user.tenant_id, with_messages=True)


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> Conversation:
    conversation = await _conversation(db, conversation_id, user.tenant_id)
    values = payload.model_dump(exclude_unset=True)
    for field, value in values.items():
        setattr(conversation, field, value)
    if values.get("title"):
        trip = await db.scalar(
            select(Trip).where(
                Trip.tenant_id == user.tenant_id,
                Trip.conversation_id == conversation.id,
            )
        )
        if trip is not None:
            trip.title = values["title"]
            trip.updated_at = utcnow()
    add_audit_event(
        db,
        user=user,
        action="conversation.updated",
        entity_type="conversation",
        entity_id=conversation.id,
        request=request,
    )
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
async def list_messages(
    conversation_id: str,
    user: CurrentUser,
    db: DBSession,
) -> list[Message]:
    conversation = await _conversation(db, conversation_id, user.tenant_id)
    return list(
        (
            await db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.tenant_id == user.tenant_id,
                )
                .order_by(Message.created_at)
            )
        ).all()
    )


@router.post("/{conversation_id}/messages", response_model=MessageRead, status_code=201)
async def create_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: CurrentUser,
    db: DBSession,
) -> Message:
    conversation = await _conversation(db, conversation_id, user.tenant_id)
    if conversation.status != "active":
        raise HTTPException(status_code=409, detail="Диалог находится в архиве")
    attachments = await _message_attachments(
        db,
        attachment_ids=payload.attachment_ids,
        tenant_id=user.tenant_id,
        conversation_id=conversation.id,
    )
    message = Message(
        tenant_id=user.tenant_id,
        conversation_id=conversation.id,
        author_id=user.id,
        role="user",
        content=payload.content,
        attachments=payload.attachment_ids,
    )
    db.add(message)
    await db.flush()
    existing_trip = await db.scalar(
        select(Trip.id).where(
            Trip.tenant_id == user.tenant_id,
            Trip.conversation_id == conversation.id,
        )
    )
    settings = request.app.state.settings
    universal_router_available = bool(
        settings.llm_provider.strip().lower() == "gemini"
        and settings.gemini_api_keys()
        and getattr(request.app.state, "trip_extraction_provider", None) is None
    )
    route_through_chat_intent = (
        (
            universal_router_available
            and (existing_trip is None or (not payload.content and bool(attachments)))
        )
        or bool(PROFILE_COMMAND_PATTERN.search(payload.content))
    )
    if route_through_chat_intent:
        for stored in attachments:
            stored.conversation_id = conversation.id
        placeholder = Message(
            tenant_id=user.tenant_id,
            conversation_id=conversation.id,
            author_id=None,
            role="assistant",
            content="Анализирую сообщение и вложения…",
            metadata_json={
                "message_type": "assistant_processing",
                "status": "processing",
                "source_message_id": message.id,
            },
        )
        db.add(placeholder)
        conversation.updated_at = utcnow()
        await db.flush()
        add_audit_event(
            db,
            user=user,
            action="message.intent_queued",
            entity_type="message",
            entity_id=message.id,
            details={"conversation_id": conversation.id},
            request=request,
        )
        await db.commit()
        await db.refresh(message)
        from ..worker import celery_app

        celery_app.send_task(
            "automation.process_chat_message",
            args=[message.id, placeholder.id],
        )
        return message

    trip = await _ensure_conversation_trip(
        db,
        conversation=conversation,
        user=user,
        content=payload.content,
        attachments=attachments,
        request=request,
    )
    workflow_run, should_run_workflow = await prepare_workflow_run(
        db,
        app=request.app,
        trip=trip,
        message=message,
        user=user,
        request=request,
    )
    add_audit_event(
        db,
        user=user,
        action="message.created",
        entity_type="message",
        entity_id=message.id,
        details={
            "conversation_id": conversation.id,
            "trip_id": trip.id,
            "workflow_run_id": workflow_run.id,
        },
        request=request,
    )
    await db.commit()
    await db.refresh(message)
    if should_run_workflow:
        dispatch_trip_workflow(
            request.app,
            workflow_run.id,
            background_tasks=background_tasks,
        )
    return message
