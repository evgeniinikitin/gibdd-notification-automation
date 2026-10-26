"""Celery wrappers around pure automation helpers.

Database-backed orchestration will enqueue IDs, not personal payloads, once the
submission repository is connected.  These initial tasks are intentionally
stateless and safe to import during worker startup.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from celery import shared_task
from sqlalchemy import select
from pypdf import PdfReader, PdfWriter
from openpyxl import load_workbook

from ..audit import add_audit_event
from .chat_intents import (
    ChatIntent,
    classify_chat_message,
    native_inline_data,
    upsert_fleet,
)
from .gemini import GeminiConfig, GeminiProvider
from .parsers import DocumentParserService
from .payload import PayloadBuilderService
from .receipts import ReceiptWorkerHelper
from .workflow import prepare_workflow_run, run_trip_workflow
from ..config import get_settings
from ..database import make_engine, make_session_factory
from ..models import Conversation, Message, StoredFile, Trip, User, utcnow
from ..storage import resolve_storage_path
from ..trip_references import next_trip_reference
from .orchestration import ConfirmedTripOrchestrator


RETRY_MESSAGE_PATTERN = re.compile(
    r"(?iu)^\s*(повтор(?:и|яй|ить)|попробуй\s+ещ[её]\s+раз|ещ[её]\s+раз)\b"
)


def _copy_batch_source(
    source: StoredFile,
    *,
    settings,
    page_range: list[int] | None,
    sheet_names: list[str] | None = None,
) -> StoredFile:
    source_path = resolve_storage_path(source.storage_key, settings=settings)
    suffix = source_path.suffix.lower() or ".bin"
    now = datetime.now(timezone.utc)
    storage_key = (
        f"{source.tenant_id}/{now:%Y/%m}/{uuid.uuid4().hex}{suffix}"
    )
    target = resolve_storage_path(storage_key, settings=settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    if suffix in {".xlsx", ".xlsm"} and sheet_names:
        workbook = load_workbook(source_path, keep_vba=suffix == ".xlsm")
        try:
            selected = set(sheet_names)
            missing = selected.difference(workbook.sheetnames)
            if missing:
                raise ValueError(
                    "В книге отсутствуют листы пакетной заявки: "
                    + ", ".join(sorted(missing))
                )
            for worksheet in list(workbook.worksheets):
                if worksheet.title not in selected:
                    workbook.remove(worksheet)
            workbook.save(target)
        finally:
            workbook.close()
    elif suffix == ".pdf" and page_range and len(page_range) == 2:
        reader = PdfReader(source_path, strict=False)
        start = max(1, page_range[0])
        end = min(len(reader.pages), page_range[1])
        if end < start:
            raise ValueError("Некорректный диапазон страниц пакетного PDF")
        writer = PdfWriter()
        for index in range(start - 1, end):
            writer.add_page(reader.pages[index])
        with target.open("wb") as output:
            writer.write(output)
    else:
        shutil.copy2(source_path, target)
    content = target.read_bytes()
    return StoredFile(
        tenant_id=source.tenant_id,
        uploaded_by_id=source.uploaded_by_id,
        kind="attachment",
        original_name=source.original_name,
        storage_key=storage_key,
        content_type=source.content_type,
        size=len(content),
        checksum_sha256=hashlib.sha256(content).hexdigest(),
    )


@shared_task(name="automation.validate_payload")
def validate_payload_task(payload: dict[str, Any]) -> dict[str, Any]:
    validated = PayloadBuilderService().validate(payload)
    return validated.model_dump(mode="json")


@shared_task(name="automation.dry_run_preflight")
def dry_run_preflight_task(payload: dict[str, Any]) -> dict[str, Any]:
    validated = PayloadBuilderService().validate(payload)
    return {
        "status": "validated",
        "tenant_id": validated.tenant_id,
        "carrier_key": validated.carrier_key,
        "would_final_submit": False,
    }


@shared_task(name="automation.extract_receipt_system_id")
def extract_receipt_system_id_task(text: str) -> str | None:
    return ReceiptWorkerHelper.extract_system_id(text)


@shared_task(
    name="automation.process_trip_workflow",
    rate_limit="12/m",
)
def process_trip_workflow_task(run_id: str) -> None:
    """Process one persisted run without sending personal data through Redis."""

    async def execute() -> None:
        settings = get_settings()
        engine = make_engine(settings.database_url)
        app = SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                engine=engine,
                session_factory=make_session_factory(engine),
            )
        )
        try:
            await run_trip_workflow(app, run_id)
        finally:
            await engine.dispose()

    asyncio.run(execute())


@shared_task(name="automation.process_chat_message", rate_limit="12/m")
def process_chat_message_task(message_id: str, placeholder_id: str) -> None:
    """Classify a generic chat message and apply profile-owned commands."""

    async def execute() -> None:
        settings = get_settings()
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        app = SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                engine=engine,
                session_factory=session_factory,
            )
        )
        try:
            async with session_factory() as db:
                message = await db.get(Message, message_id)
                placeholder = await db.get(Message, placeholder_id)
                if message is None or placeholder is None:
                    return
                if (placeholder.metadata_json or {}).get("status") != "processing":
                    return
                user = await db.get(User, message.author_id)
                conversation = await db.get(Conversation, message.conversation_id)
                if user is None or conversation is None:
                    return

                files = list(
                    (
                        await db.scalars(
                            select(StoredFile).where(
                                StoredFile.tenant_id == message.tenant_id,
                                StoredFile.id.in_(message.attachments or []),
                                StoredFile.is_deleted.is_(False),
                            )
                        )
                    ).all()
                )
                if not files and RETRY_MESSAGE_PATTERN.search(message.content or ""):
                    prior_messages = list(
                        (
                            await db.scalars(
                                select(Message)
                                .where(
                                    Message.tenant_id == message.tenant_id,
                                    Message.conversation_id == message.conversation_id,
                                    Message.role == "user",
                                    Message.id != message.id,
                                )
                                .order_by(Message.created_at.desc())
                            )
                        ).all()
                    )
                    inherited_ids = next(
                        (
                            list(item.attachments or [])
                            for item in prior_messages
                            if item.attachments
                        ),
                        [],
                    )
                    if inherited_ids:
                        message.attachments = inherited_ids
                        files = list(
                            (
                                await db.scalars(
                                    select(StoredFile).where(
                                        StoredFile.tenant_id == message.tenant_id,
                                        StoredFile.id.in_(inherited_ids),
                                        StoredFile.is_deleted.is_(False),
                                    )
                                )
                            ).all()
                        )
                provider = GeminiProvider(
                    GeminiConfig(
                        api_key=settings.gemini_api_keys()[0],
                        api_keys=settings.gemini_api_keys()[1:],
                        base_url=settings.gemini_base_url,
                        model=settings.gemini_model,
                        proxy_url=settings.gemini_proxy_url or None,
                    )
                )
                parser = DocumentParserService(image_text_extractor=provider)
                documents: list[tuple[str, str, str, str | None]] = []
                for stored in files:
                    path = resolve_storage_path(stored.storage_key, settings=settings)
                    try:
                        parsed = await parser.parse(path)
                    except Exception as exc:
                        documents.append(
                            (
                                stored.original_name,
                                stored.content_type,
                                (
                                    "[Файл не удалось прочитать: "
                                    f"{type(exc).__name__}. Остальные файлы "
                                    "нужно обработать независимо.]"
                                ),
                                native_inline_data(path, stored.content_type),
                            )
                        )
                    else:
                        documents.append(
                            (
                                stored.original_name,
                                parsed.media_type,
                                parsed.text,
                                native_inline_data(path, parsed.media_type),
                            )
                        )

                try:
                    routed = await classify_chat_message(
                        provider,
                        content=message.content,
                        documents=documents,
                    )
                finally:
                    await provider.aclose()

                result = routed.data
                if result.intent == ChatIntent.FLEET_UPDATE:
                    if user.role not in {"client_admin", "support_admin", "admin"}:
                        placeholder.content = "Недостаточно прав для изменения автопарка."
                    else:
                        counts = await upsert_fleet(
                            db,
                            tenant_id=message.tenant_id,
                            result=result,
                        )
                        placeholder.content = (
                            "Данные автопарка обновлены. "
                            f"Автобусы: добавлено {counts['vehicles_created']}, "
                            f"обновлено {counts['vehicles_updated']}, "
                            f"не добавлено {counts['vehicles_skipped']}. "
                            f"Водители: добавлено {counts['drivers_created']}, "
                            f"обновлено {counts['drivers_updated']}."
                            + (
                                f" {counts['vehicles_skipped']} новых автобус(а/ов) "
                                "распознаны, но в файле для них нет вместимости — "
                                "обязательного поля для безопасного назначения."
                                if counts["vehicles_skipped"]
                                else ""
                            )
                            + (
                                f" Внимание: у {counts['vehicles_expired']} "
                                "автобус(а/ов) карта отсутствует или истекла; "
                                "они отмечены красным и не будут назначаться."
                                if counts["vehicles_expired"]
                                else ""
                            )
                        )
                        placeholder.metadata_json = {
                            **(placeholder.metadata_json or {}),
                            "import_counts": counts,
                        }
                        add_audit_event(
                            db,
                            user=user,
                            action="fleet.imported_from_chat",
                            entity_type="message",
                            entity_id=message.id,
                            details=counts,
                        )
                elif (
                    result.intent == ChatIntent.BATCH_TRIP_REQUEST
                    and len(result.trip_groups) > 1
                ):
                    files_by_name = {item.original_name: item for item in files}
                    used_file_ids: set[str] = set()
                    created: list[tuple[Trip, Message, str]] = []
                    for group in result.trip_groups:
                        group_files: list[StoredFile] = []
                        for name in group.source_file_names:
                            source = files_by_name.get(name)
                            if source is None:
                                continue
                            page_range = group.source_page_ranges.get(name)
                            sheet_names = group.source_sheet_names.get(name)
                            if page_range or sheet_names or source.id in used_file_ids:
                                derived = _copy_batch_source(
                                    source,
                                    settings=settings,
                                    page_range=page_range,
                                    sheet_names=sheet_names,
                                )
                                db.add(derived)
                                await db.flush()
                                group_files.append(derived)
                            else:
                                group_files.append(source)
                        if not group_files:
                            continue
                        child = Conversation(
                            tenant_id=message.tenant_id,
                            created_by_id=user.id,
                            title=group.title,
                            status="active",
                        )
                        db.add(child)
                        await db.flush()
                        child_message = Message(
                            tenant_id=message.tenant_id,
                            conversation_id=child.id,
                            author_id=user.id,
                            role="user",
                            content=group.extraction_hint,
                            attachments=[item.id for item in group_files],
                        )
                        db.add(child_message)
                        trip = Trip(
                            reference=await next_trip_reference(db),
                            tenant_id=message.tenant_id,
                            conversation_id=child.id,
                            created_by_id=user.id,
                            title=group.title,
                            status="draft",
                            progress=0,
                        )
                        db.add(trip)
                        await db.flush()
                        for stored in group_files:
                            used_file_ids.add(stored.id)
                            stored.conversation_id = child.id
                            stored.trip_id = trip.id
                        run, should_run = await prepare_workflow_run(
                            db,
                            app=app,
                            trip=trip,
                            message=child_message,
                            user=user,
                        )
                        if should_run:
                            created.append((trip, child_message, run.id))
                    placeholder.content = (
                        f"Разделил пакет на {len(created)} самостоятельных заявок. "
                        "Для каждой создан отдельный диалог и запущено распознавание."
                    )
                    placeholder.metadata_json = {
                        **(placeholder.metadata_json or {}),
                        "status": "complete",
                        "intent": result.intent.value,
                        "created_trip_ids": [item[0].id for item in created],
                    }
                    await db.commit()
                    for _, _, run_id in created:
                        await run_trip_workflow(app, run_id)
                    return
                elif result.intent in {
                    ChatIntent.TRIP_REQUEST,
                    ChatIntent.TRIP_CLARIFICATION,
                }:
                    trip = await db.scalar(
                        select(Trip).where(
                            Trip.tenant_id == message.tenant_id,
                            Trip.conversation_id == message.conversation_id,
                        )
                    )
                    if trip is None:
                        title = message.content.strip() or (
                            files[0].original_name if files else "Новая заявка"
                        )
                        trip = Trip(
                            reference=await next_trip_reference(db),
                            tenant_id=message.tenant_id,
                            conversation_id=message.conversation_id,
                            created_by_id=user.id,
                            title=title[:255],
                            status="draft",
                            progress=0,
                        )
                        db.add(trip)
                        await db.flush()
                    for stored in files:
                        stored.conversation_id = message.conversation_id
                        stored.trip_id = trip.id
                    run, should_run = await prepare_workflow_run(
                        db,
                        app=app,
                        trip=trip,
                        message=message,
                        user=user,
                    )
                    placeholder.content = "Определил документы как заявку на поездку. Распознаю данные."
                    placeholder.metadata_json = {
                        **(placeholder.metadata_json or {}),
                        "workflow_run_id": run.id,
                    }
                    placeholder.metadata_json["status"] = "complete"
                    await db.commit()
                    if should_run:
                        await run_trip_workflow(app, run.id)
                    return
                elif result.intent == ChatIntent.PROFILE_UPDATE:
                    placeholder.content = (
                        "Я распознал реквизиты профиля, но автоматическое изменение "
                        "основных реквизитов требует отдельного подтверждения. "
                        "Автобусы и водителей можно обновлять через чат автоматически."
                    )
                else:
                    placeholder.content = result.answer or (
                        "Не удалось однозначно определить действие. Уточните, что нужно сделать."
                    )

                placeholder.metadata_json = {
                    **(placeholder.metadata_json or {}),
                    "status": "complete",
                    "intent": result.intent.value,
                    "provider": routed.provider,
                    "model": routed.actual_model,
                }
                conversation.updated_at = utcnow()
                await db.commit()
        except Exception as exc:
            async with session_factory() as db:
                placeholder = await db.get(Message, placeholder_id)
                if placeholder is not None:
                    placeholder.content = (
                        "Не удалось обработать вложение. Данные не изменены; "
                        "можно безопасно повторить попытку."
                    )
                    placeholder.metadata_json = {
                        **(placeholder.metadata_json or {}),
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                    await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(execute())


@shared_task(name="automation.process_confirmed_trip", queue="browser")
def process_confirmed_trip_task(job_id: str) -> dict[str, Any]:
    """Generate and dry-run one persisted job without queueing personal data."""

    async def execute() -> dict[str, Any]:
        settings = get_settings()
        engine = make_engine(settings.database_url)
        session_factory = make_session_factory(engine)
        try:
            async with session_factory() as db:
                job = await ConfirmedTripOrchestrator(settings=settings).run(
                    db,
                    job_id=job_id,
                )
                return {
                    "job_id": job.id,
                    "trip_id": job.trip_id,
                    "status": job.status,
                    "final_submit_attempted": job.final_submit_attempted,
                }
        finally:
            await engine.dispose()

    return asyncio.run(execute())
