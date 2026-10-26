"""Persisted chat -> parser/OCR -> LLM -> clarification orchestration."""

from __future__ import annotations

import base64
import re
import asyncio
import copy
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import add_audit_event
from ..config import Settings
from ..models import (
    CarrierProfile,
    Driver,
    Message,
    StoredFile,
    Trip,
    User,
    Vehicle,
    utcnow,
)
from ..schemas import TripRead
from ..storage import resolve_storage_path
from .exceptions import DocumentParseError, GeminiError, OpenRouterError
from .customer_memory import enrich_customer_from_memory
from .extraction import (
    ExtractionDocumentDTO,
    OpenRouterTripExtractionProvider,
    MockTripExtractionProvider,
    TripExtractionContextDTO,
    TripExtractionDTO,
    TripExtractionProvider,
    derive_missing_transfer_end_time,
    normalize_extracted_passenger_roles,
)
from .openrouter import OpenRouterConfig, OpenRouterProvider
from .gemini import (
    GeminiConfig,
    GeminiFallbackProvider,
    GeminiProvider,
)
from .parsers import DocumentParserService
from .route_memory import enrich_extracted_route_from_memory
from .workflow_models import TripWorkflowRun


ACTIVE_RUN_STATUSES = {"queued", "extracting"}
TERMINAL_RUN_STATUSES = {
    "config_required",
    "clarifying",
    "needs_review",
    "completed",
    "failed",
    "superseded",
}
PROTECTED_TRIP_STATUSES = {
    "confirmed",
    "validated",
    "generating",
    "generating_documents",
    "building_payload",
    "ready_to_submit",
    "submitting",
    "captcha_required",
    "dry_run_ready",
    "submitted",
    "receipt_pending",
    "completed",
    "cancelled",
}
CONFIDENCE_THRESHOLD = 0.75
SYSTEM_DERIVED_FIELDS = {
    "customer.email",
    "route.district",
    "route.roads",
    "route.distance",
    "route.duration",
}


def _provider_is_configured(app: FastAPI) -> bool:
    if getattr(app.state, "trip_extraction_provider", None) is not None:
        return True
    settings: Settings = app.state.settings
    provider = settings.llm_provider.strip().lower()
    if provider == "gemini":
        return bool(settings.gemini_api_keys())
    if provider == "openrouter":
        return bool(settings.openrouter_api_key.strip())
    if provider in {"mock", "rule_based"}:
        return settings.app_env.strip().lower() != "production"
    return False


def _configuration_message(settings: Settings) -> str:
    provider = settings.llm_provider.strip().lower()
    if provider == "gemini":
        return (
            "Для автоматического разбора нужно добавить GEMINI_API_KEY "
            "в файл .env и перезапустить API. Сообщение и вложения сохранены; "
            "после настройки запустите разбор повторно."
        )
    if provider == "openrouter":
        return (
            "Для автоматического разбора нужно добавить OPENROUTER_API_KEY "
            "в файл .env и перезапустить API. Сообщение и вложения сохранены; "
            "после настройки запустите разбор повторно."
        )
    return (
        f"LLM-провайдер «{provider or 'не указан'}» пока не настроен. "
        "Сообщение и вложения сохранены."
    )


async def prepare_workflow_run(
    db: AsyncSession,
    *,
    app: FastAPI,
    trip: Trip,
    message: Message,
    user: User,
    request: Request | None = None,
) -> tuple[TripWorkflowRun, bool]:
    """Persist a queued/config-required run in the message transaction."""

    revision = (
        await db.scalar(
            select(func.max(TripWorkflowRun.revision)).where(
                TripWorkflowRun.trip_id == trip.id
            )
        )
        or 0
    ) + 1
    settings: Settings = app.state.settings
    protected = trip.status in PROTECTED_TRIP_STATUSES
    configured = _provider_is_configured(app)
    if protected:
        status = "completed"
        error_code = None
        error_message = None
        result_json = {
            "reason": "trip_is_already_confirmed_or_processing",
            "can_confirm": False,
        }
    elif not configured:
        status = "config_required"
        error_code = "llm_config_required"
        error_message = _configuration_message(settings)
        result_json = {
            "configuration_required": True,
            "can_confirm": False,
        }
    else:
        status = "queued"
        error_code = None
        error_message = None
        result_json = {}

    run = TripWorkflowRun(
        tenant_id=user.tenant_id,
        trip_id=trip.id,
        message_id=message.id,
        triggered_by_id=user.id,
        revision=revision,
        status=status,
        requested_provider=settings.llm_provider,
        requested_model=(
            settings.gemini_model
            if settings.llm_provider.strip().lower() == "gemini"
            else (
                settings.openrouter_model
                if settings.llm_provider.strip().lower() == "openrouter"
                else None
            )
        ),
        error_code=error_code,
        error_message=error_message,
        result_json=result_json,
        finished_at=utcnow() if status != "queued" else None,
    )
    db.add(run)
    await db.flush()
    message.metadata_json = {
        **(message.metadata_json or {}),
        "trip_id": trip.id,
        "workflow_run_id": run.id,
        "workflow_revision": revision,
    }

    if status == "config_required":
        # Keep the legal/business trip in draft. Configuration is the state of
        # this processing run, not a fabricated lifecycle transition.
        trip.status = "draft"
        assistant = Message(
            tenant_id=user.tenant_id,
            conversation_id=message.conversation_id,
            author_id=None,
            role="assistant",
            content=error_message or "LLM-провайдер не настроен",
            metadata_json={
                "message_type": "system_status",
                "status": "config_required",
                "workflow_run_id": run.id,
                "configuration_required": True,
            },
        )
        db.add(assistant)
    elif status == "queued":
        trip.status = "extracting"
        trip.progress = max(trip.progress, 5)

    add_audit_event(
        db,
        user=user,
        action=(
            "trip.extraction_queued"
            if status == "queued"
            else "trip.extraction_not_queued"
        ),
        entity_type="trip_workflow_run",
        entity_id=run.id,
        details={
            "trip_id": trip.id,
            "message_id": message.id,
            "revision": revision,
            "status": status,
            "error_code": error_code,
        },
        request=request,
    )
    return run, status == "queued"


@asynccontextmanager
async def _provider_for_app(app: FastAPI):
    injected = getattr(app.state, "trip_extraction_provider", None)
    if injected is not None:
        yield injected, injected
        return

    settings: Settings = app.state.settings
    provider_name = settings.llm_provider.strip().lower()
    if provider_name in {"mock", "rule_based"}:
        if settings.app_env.strip().lower() == "production":
            raise RuntimeError("Mock LLM provider is forbidden in production")
        provider = MockTripExtractionProvider()
        yield provider, provider
        return
    if provider_name == "gemini":
        gemini = GeminiProvider(
            GeminiConfig(
                api_key=settings.gemini_api_keys()[0],
                api_keys=settings.gemini_api_keys()[1:],
                base_url=settings.gemini_base_url,
                model=settings.gemini_model,
                proxy_url=settings.gemini_proxy_url or None,
            )
        )
        openrouter = None
        openrouter_extraction = None
        if settings.openrouter_api_key.strip():
            openrouter = OpenRouterProvider(
                OpenRouterConfig(
                    api_key=settings.openrouter_api_key,
                    base_url=settings.openrouter_base_url,
                    model=settings.openrouter_model,
                    captcha_model=settings.openrouter_captcha_model,
                    http_referer=settings.openrouter_http_referer or None,
                    app_title=settings.openrouter_app_title,
                    proxy_url=settings.openrouter_proxy_url or None,
                )
            )
            openrouter_extraction = OpenRouterTripExtractionProvider(
                openrouter
            )
        provider = GeminiFallbackProvider(
            gemini,
            fallback=openrouter_extraction,
            fallback_image_extractor=openrouter,
        )
        try:
            yield provider, provider
        finally:
            await gemini.aclose()
            if openrouter is not None:
                await openrouter.aclose()
        return
    if provider_name != "openrouter":
        raise RuntimeError(f"Unsupported LLM provider: {settings.llm_provider}")
    raw = OpenRouterProvider(
        OpenRouterConfig(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            model=settings.openrouter_model,
            captcha_model=settings.openrouter_captcha_model,
            http_referer=settings.openrouter_http_referer or None,
            app_title=settings.openrouter_app_title,
            proxy_url=settings.openrouter_proxy_url or None,
        )
    )
    try:
        yield OpenRouterTripExtractionProvider(raw), raw
    finally:
        await raw.aclose()


async def _documents_for_run(
    db: AsyncSession,
    *,
    app: FastAPI,
    trip: Trip,
    image_extractor: Any,
    native_pdf: bool = False,
) -> list[ExtractionDocumentDTO]:
    files = list(
        (
            await db.scalars(
                select(StoredFile)
                .where(
                    StoredFile.tenant_id == trip.tenant_id,
                    StoredFile.trip_id == trip.id,
                    StoredFile.kind == "attachment",
                    StoredFile.is_deleted.is_(False),
                )
                .order_by(StoredFile.created_at, StoredFile.id)
            )
        ).all()
    )
    parser = DocumentParserService(
        image_text_extractor=None if native_pdf else image_extractor
    )
    parsed_documents: list[ExtractionDocumentDTO] = []
    for stored in files:
        path = resolve_storage_path(
            stored.storage_key, settings=app.state.settings
        )
        try:
            parsed = await parser.parse(path)
            parsed_documents.append(
                ExtractionDocumentDTO(
                    file_id=stored.id,
                    name=stored.original_name,
                    media_type=parsed.media_type,
                    text=parsed.text,
                    warnings=parsed.warnings,
                    inline_data_base64=(
                        base64.b64encode(path.read_bytes()).decode("ascii")
                        if native_pdf
                        and (
                            parsed.media_type == "application/pdf"
                            or parsed.media_type.startswith("image/")
                        )
                        else None
                    ),
                )
            )
        except DocumentParseError as exc:
            parsed_documents.append(
                ExtractionDocumentDTO(
                    file_id=stored.id,
                    name=stored.original_name,
                    media_type=stored.content_type,
                    text="",
                    warnings=[f"parse_error:{type(exc).__name__}"],
                )
            )
    return _budget_documents_for_llm(
        parsed_documents,
        total_budget=app.state.settings.llm_max_document_context_chars,
        per_document_budget=app.state.settings.llm_max_chars_per_document,
    )


def _bounded_text(value: str, budget: int) -> tuple[str, bool]:
    if len(value) <= budget:
        return value, False
    if budget <= 0:
        return "", bool(value)
    marker = "\n\n[… текст сокращён в пределах LLM-контекста …]\n\n"
    if budget <= len(marker):
        return value[:budget], True
    usable = max(0, budget - len(marker))
    head_size = int(usable * 0.7)
    tail_size = usable - head_size
    return (
        value[:head_size] + marker + (value[-tail_size:] if tail_size else ""),
        True,
    )


def _budget_documents_for_llm(
    documents: list[ExtractionDocumentDTO],
    *,
    total_budget: int,
    per_document_budget: int,
) -> list[ExtractionDocumentDTO]:
    """Share a deterministic prompt budget while preserving head and tail."""

    remaining = total_budget
    bounded: list[ExtractionDocumentDTO] = []
    for index, document in enumerate(documents):
        documents_left = len(documents) - index
        fair_share = remaining // documents_left if documents_left else 0
        budget = max(0, min(per_document_budget, fair_share, remaining))
        original_length = len(document.text)
        text, truncated = _bounded_text(document.text, budget)
        warnings = list(document.warnings)
        if truncated:
            warnings.append(
                f"llm_text_truncated:{original_length}:{len(text)}"
            )
        bounded.append(
            document.model_copy(
                update={"text": text, "warnings": warnings}
            )
        )
        remaining -= len(text)
    return bounded


def _budget_recent_messages(
    messages: Iterable[str], *, total_budget: int
) -> list[str]:
    """Keep the newest messages, truncating each from head+tail if necessary."""

    selected: list[str] = []
    remaining = total_budget
    values = [value for value in messages if value]
    for value in reversed(values):
        if remaining <= 0:
            break
        bounded, _ = _bounded_text(value, remaining)
        selected.append(bounded)
        remaining -= len(bounded)
    selected.reverse()
    return selected


def _clean_optional(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = " ".join(value.split())
    if normalized.lower() in {
        "",
        "-",
        "—",
        "не указано",
        "нет данных",
        "отсутствует",
        "null",
    }:
        return None
    return normalized


def _merge_non_empty(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, raw_value in source.items():
        value = _clean_optional(raw_value)
        if value is None:
            continue
        if isinstance(value, dict):
            child = target.get(key)
            if not isinstance(child, dict):
                child = {}
            _merge_non_empty(child, value)
            target[key] = child
        elif isinstance(value, list):
            if value:
                target[key] = value
        else:
            target[key] = value


def _customer_from_agent_document(
    documents: list[ExtractionDocumentDTO],
) -> dict[str, Any] | None:
    """Resolve the ordering agent when an agency appendix names both parties."""

    for document in documents:
        text = document.text
        if not re.search(r"(?iu)агентск(?:ий|ому|ого|им|ом)", text):
            continue
        agent_block = re.search(
            r"(?isu)(?<!принцип)\bагент\s*:\s*(.{0,800})",
            text,
        )
        if agent_block is None:
            continue
        party = re.search(
            r"(?iu)\b(ООО|АО|ПАО|ИП)\s*[«\"]?([^\n\r«»\"]{2,120})[»\"]?",
            agent_block.group(1),
        )
        if party is None:
            continue
        kind = party.group(1).upper()
        name = " ".join(party.group(2).split()).strip(" .,_—-")
        if not name:
            continue
        display_name = f"{kind} «{name}»" if kind != "ИП" else f"ИП {name}"
        inn_match = re.search(
            r"(?iu)\bИНН\s*[:№]?\s*(\d{10}|\d{12})\b",
            agent_block.group(1),
        )
        return {
            "type": "ip" if kind == "ИП" else "legal",
            "name": display_name,
            **({"inn": inn_match.group(1)} if inn_match else {}),
        }
    return None


def _responsible_phone_from_documents(
    documents: list[ExtractionDocumentDTO],
) -> str | None:
    """Prefer the trip's explicitly labelled responsible contact."""

    phone_pattern = r"(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"
    labels = (
        r"ответственн(?:ое\s+лицо|ый|ая)|"
        r"ответственн(?:ый|ая)\s+за\s+поездку|"
        r"контактное\s+лицо"
    )
    for document in documents:
        match = re.search(
            rf"(?:{labels})[^\n]{{0,240}}?({phone_pattern})",
            document.text,
            re.IGNORECASE,
        )
        if match is None:
            continue
        digits = re.sub(r"\D", "", match.group(1))
        if len(digits) == 11 and digits[0] in "78":
            return "+7" + digits[1:]
        if len(digits) == 10:
            return "+7" + digits
    return None


def _canonical_from_result(
    *,
    current: dict[str, Any],
    extraction: TripExtractionDTO,
    revision: int,
    source_documents: list[ExtractionDocumentDTO],
    llm_metadata: dict[str, Any],
) -> dict[str, Any]:
    existing = dict(current) if current.get("schema_version") == 1 else {}
    canonical: dict[str, Any] = {
        "schema_version": 1,
        "customer": dict(existing.get("customer") or {}),
        "trip": dict(existing.get("trip") or {}),
        "route": dict(existing.get("route") or {}),
        "children": list(existing.get("children") or []),
        "companions": list(existing.get("companions") or []),
        "transport_preferences": dict(
            existing.get("transport_preferences") or {}
        ),
        "slot_states": dict(existing.get("slot_states") or {}),
        "missing_clarifications": [],
        "uncertain_fields": list(existing.get("uncertain_fields") or []),
        "ignored_carrier_mentions": list(
            existing.get("ignored_carrier_mentions") or []
        ),
    }
    raw = extraction.model_dump(mode="json", exclude_none=True)
    if extraction.customer is not None:
        _merge_non_empty(canonical["customer"], raw["customer"])
    trip_values = {
        "trip_kind": (
            raw.get("trip_kind")
            if raw.get("trip_kind") != "unknown"
            else None
        ),
        "small_group_notification_confirmed": raw.get(
            "small_group_notification_confirmed"
        ),
        "trip_date": raw.get("trip_date"),
        "start_time": raw.get("start_time"),
        "end_time": raw.get("end_time"),
        "children_count": raw.get("children_count"),
        "companions_count": raw.get("companions_count"),
    }
    if extraction.children:
        trip_values["children_count"] = len(extraction.children)
    if extraction.companions:
        trip_values["companions_count"] = max(
            raw.get("companions_count") or 0,
            len(extraction.companions),
        )
    _merge_non_empty(canonical["trip"], trip_values)
    if extraction.route is not None:
        _merge_non_empty(canonical["route"], raw["route"])
    if extraction.children:
        canonical["children"] = raw["children"]
    if extraction.companions:
        canonical["companions"] = raw["companions"]
    if extraction.transport_preferences is not None:
        _merge_non_empty(
            canonical["transport_preferences"],
            raw["transport_preferences"],
        )

    agency_customer = _customer_from_agent_document(source_documents)
    if agency_customer is not None:
        canonical["customer"] = agency_customer
    responsible_phone = _responsible_phone_from_documents(source_documents)
    if responsible_phone is not None:
        canonical["customer"]["phone"] = responsible_phone
    customer_memory_match = enrich_customer_from_memory(canonical["customer"])
    if customer_memory_match:
        llm_metadata["customer_memory_match"] = customer_memory_match

    confirmed = set(extraction.confirmed_fields)
    uncertain = (
        set(canonical["uncertain_fields"])
        | set(extraction.uncertain_fields)
        | {
            field
            for field, confidence in extraction.confidence.items()
            if confidence < CONFIDENCE_THRESHOLD
        }
    )
    uncertain -= confirmed
    canonical["uncertain_fields"] = sorted(uncertain)

    values_by_path = _flatten_canonical(canonical)
    for field, value in values_by_path.items():
        if value in (None, "", []):
            continue
        prior = canonical["slot_states"].get(field, {})
        confidence = extraction.confidence.get(
            field, prior.get("confidence", 1.0)
        )
        if field in confirmed:
            status = "confirmed"
            source = "user_message"
        elif prior.get("status") == "confirmed":
            status = "confirmed"
            source = prior.get("source", "user_message")
        elif field in uncertain:
            status = "needs_confirmation"
            source = "llm"
        else:
            status = "extracted"
            source = "llm"
        canonical["slot_states"][field] = {
            "status": status,
            "source": source,
            "confidence": confidence,
            "revision": revision,
        }

    for mention in extraction.ignored_carrier_mentions:
        cleaned = _clean_optional(mention)
        if cleaned and cleaned not in canonical["ignored_carrier_mentions"]:
            canonical["ignored_carrier_mentions"].append(cleaned)
    canonical["source_documents"] = [
        {
            "file_id": document.file_id,
            "name": document.name,
            "media_type": document.media_type,
            "warnings": document.warnings,
        }
        for document in source_documents
    ]
    canonical["llm"] = llm_metadata
    canonical["workflow"] = {"revision": revision}
    return canonical


def _flatten_canonical(data: dict[str, Any]) -> dict[str, Any]:
    customer = data.get("customer") or {}
    trip = data.get("trip") or {}
    route = data.get("route") or {}
    preferences = data.get("transport_preferences") or {}
    return {
        **{f"customer.{key}": value for key, value in customer.items()},
        **{f"trip.{key}": value for key, value in trip.items()},
        **{f"route.{key}": value for key, value in route.items()},
        "children": data.get("children") or [],
        "companions": data.get("companions") or [],
        **{
            f"transport_preferences.{key}": value
            for key, value in preferences.items()
        },
    }


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _duration_between(start: Any, end: Any) -> str | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        start_hour, start_minute = (int(item) for item in start.split(":"))
        end_hour, end_minute = (int(item) for item in end.split(":"))
    except (TypeError, ValueError):
        return None
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute
    minutes = end_total - start_total
    if minutes <= 0:
        minutes += 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _time_after_duration(start: Any, duration: Any) -> str | None:
    if not isinstance(start, str) or not isinstance(duration, str):
        return None
    try:
        start_hour, start_minute = (int(item) for item in start.split(":"))
        duration_hour, duration_minute = (
            int(item) for item in duration.split(":")
        )
        if start_hour > 23 or start_minute > 59 or duration_minute > 59:
            return None
    except (TypeError, ValueError):
        return None
    arrival = datetime(2000, 1, 1, start_hour, start_minute) + timedelta(
        hours=duration_hour,
        minutes=duration_minute,
    )
    return arrival.strftime("%H:%M")


def _classic_route_text(route: dict[str, Any]) -> str | None:
    distance = _clean_optional(route.get("distance"))
    duration = _clean_optional(route.get("duration"))
    address_start = _clean_optional(route.get("address_start"))
    roads = _clean_optional(route.get("roads"))
    if not all((distance, duration, address_start, roads)):
        return None
    if str(roads).casefold().startswith("на автомобиле"):
        return str(roads)

    distance_text = re.sub(
        r"(?iu)\s*(?:км|километр(?:а|ов)?)\s*$", "", str(distance)
    ).strip()
    road_lines = [
        item.strip(" .;–—-")
        for item in re.split(r"[,;\n]+", str(roads))
        if item.strip(" .;–—-")
    ]
    points = [address_start]
    points.extend(
        item.get("address")
        for item in route.get("stops") or []
        if isinstance(item, dict) and _clean_optional(item.get("address"))
    )
    destination = _clean_optional(route.get("destination"))
    if destination and destination not in points:
        points.append(destination)
    return "\n".join(
        dict.fromkeys(
            [
                f"На автомобиле около {distance_text} км, {duration} без учета пробок.",
                address_start,
                *road_lines,
                *[str(item) for item in points[1:]],
            ]
        )
    )


async def _apply_system_derived_fields(
    db: AsyncSession,
    *,
    trip: Trip,
    canonical: dict[str, Any],
) -> None:
    """Apply profile-owned and deterministic values before clarification."""

    customer = canonical.setdefault("customer", {})
    trip_data = canonical.setdefault("trip", {})
    route = canonical.setdefault("route", {})
    slot_states = canonical.setdefault("slot_states", {})

    profile = await db.scalar(
        select(CarrierProfile).where(CarrierProfile.tenant_id == trip.tenant_id)
    )
    if profile is not None and profile.email:
        customer["email"] = profile.email
        slot_states["customer.email"] = {
            "status": "system",
            "source": "carrier_profile",
            "confidence": 1.0,
            "revision": (canonical.get("workflow") or {}).get("revision"),
        }

    if profile is not None:
        carrier_names = {
            " ".join(value.casefold().split())
            for value in (profile.legal_name, profile.short_name)
            if value
        }
        customer_name = " ".join(str(customer.get("name") or "").casefold().split())
        customer_inn = str(customer.get("inn") or "").strip()
        carrier_was_selected = bool(
            (customer_inn and profile.inn and customer_inn == profile.inn)
            or (customer_name and customer_name in carrier_names)
        )
        if carrier_was_selected:
            mention = customer.get("name") or customer.get("inn")
            ignored = canonical.setdefault("ignored_carrier_mentions", [])
            if mention and mention not in ignored:
                ignored.append(mention)
            canonical["customer"] = ({"email": profile.email} if profile.email else {})
            customer = canonical["customer"]

    await _enrich_customer_from_trip_history(
        db,
        trip=trip,
        canonical=canonical,
    )

    if not route.get("district"):
        route_source = " ".join(
            str(value)
            for value in (
                route.get("address_start"),
                route.get("text"),
            )
            if value
        )
        match = re.search(
            r"(?iu)\b([\wЁё-]+\s+район)\b",
            route_source,
        )
        if match:
            route["district"] = match.group(1)

    if not route.get("roads") and route.get("text"):
        route["roads"] = f"Ориентировочно по маршруту: {route['text']}"
        slot_states["route.roads"] = {
            "status": "system",
            "source": "route_estimate",
            "confidence": 0.8,
            "revision": (canonical.get("workflow") or {}).get("revision"),
        }

    if (
        not route.get("duration")
        and trip_data.get("trip_kind") == "transfer"
    ):
        duration = _duration_between(
            trip_data.get("start_time"),
            trip_data.get("end_time"),
        )
        if duration:
            route["duration"] = duration

    classic_roads = _classic_route_text(route)
    if classic_roads:
        route["roads"] = classic_roads
        slot_states["route.roads"] = {
            "status": "system",
            "source": "classic_route_formatter",
            "confidence": 0.9,
            "revision": (canonical.get("workflow") or {}).get("revision"),
        }

    if trip_data.get("trip_kind") == "transfer" and not trip_data.get("end_time"):
        end_time = _time_after_duration(
            trip_data.get("start_time"),
            route.get("duration"),
        )
        if end_time:
            trip_data["end_time"] = end_time
            slot_states["trip.end_time"] = {
                "status": "system",
                "source": "transfer_duration_estimate",
                "confidence": 0.85,
                "revision": (canonical.get("workflow") or {}).get("revision"),
            }

    system_derived_fields = set(SYSTEM_DERIVED_FIELDS)
    if trip_data.get("trip_kind") == "transfer":
        system_derived_fields.add("trip.end_time")
    canonical["uncertain_fields"] = [
        field
        for field in canonical.get("uncertain_fields", [])
        if field not in system_derived_fields
    ]


def _normalize_plate(value: str) -> str:
    return re.sub(r"[\s-]+", "", value.upper())


def _normalize_party_name(value: Any) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", "", str(value or "").casefold())


async def _enrich_customer_from_trip_history(
    db: AsyncSession,
    *,
    trip: Trip,
    canonical: dict[str, Any],
) -> None:
    customer = canonical.setdefault("customer", {})
    normalized_name = _normalize_party_name(customer.get("name"))
    if not normalized_name:
        return
    history = list(
        (
            await db.scalars(
                select(Trip)
                .where(
                    Trip.tenant_id == trip.tenant_id,
                    Trip.id != trip.id,
                )
                .order_by(Trip.updated_at.desc())
                .limit(100)
            )
        ).all()
    )
    slot_states = canonical.setdefault("slot_states", {})
    for prior_trip in history:
        prior = (prior_trip.extracted_data or {}).get("customer") or {}
        if _normalize_party_name(prior.get("name")) != normalized_name:
            continue
        for field in ("inn", "address", "phone"):
            if not customer.get(field) and prior.get(field):
                customer[field] = prior[field]
                slot_states[f"customer.{field}"] = {
                    "status": "system",
                    "source": "customer_history",
                    "confidence": 1.0,
                    "revision": (canonical.get("workflow") or {}).get("revision"),
                }
        if all(customer.get(field) for field in ("inn", "address", "phone")):
            break


async def _apply_transport_preferences(
    db: AsyncSession,
    *,
    trip: Trip,
    canonical: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    preferences = canonical.get("transport_preferences") or {}
    trip_date = _as_date((canonical.get("trip") or {}).get("trip_date"))

    vehicles = list(
        (
            await db.scalars(
                select(Vehicle)
                .where(
                    Vehicle.tenant_id == trip.tenant_id,
                    Vehicle.is_active.is_(True),
                )
                .order_by(Vehicle.registration_plate)
            )
        ).all()
    )
    valid_vehicles = [
        vehicle
        for vehicle in vehicles
        if trip_date is None
        or (
            vehicle.diagnostic_card_valid_until is not None
            and trip_date < vehicle.diagnostic_card_valid_until
        )
    ]
    if trip.selected_vehicle_id is not None and not any(
        vehicle.id == trip.selected_vehicle_id for vehicle in valid_vehicles
    ):
        trip.selected_vehicle_id = None
        issues.append(
            _clarification(
                "transport.vehicle",
                "Автобус",
                (
                    "Ранее выбранный автобус больше недоступен или его "
                    "диагностическая карта недействительна на дату поездки. "
                    "Выберите другой автобус."
                ),
                reason="selected_vehicle_became_unavailable",
            )
        )
    requested_plate = _clean_optional(
        preferences.get("vehicle_registration_plate")
    )
    if requested_plate:
        match = next(
            (
                vehicle
                for vehicle in valid_vehicles
                if _normalize_plate(vehicle.registration_plate)
                == _normalize_plate(requested_plate)
            ),
            None,
        )
        if match is None:
            issues.append(
                _clarification(
                    "transport.vehicle",
                    "Автобус",
                    (
                        f"Автобус {requested_plate} не найден среди доступных "
                        "автобусов профиля с действующей диагностической картой. "
                        "Выберите другой автобус в профиле."
                    ),
                    reason="requested_vehicle_unavailable",
                )
            )
        else:
            trip.selected_vehicle_id = match.id
            issues = [
                item for item in issues if item["field"] != "transport.vehicle"
            ]
    elif trip.selected_vehicle_id is None:
        if len(valid_vehicles) == 1:
            trip.selected_vehicle_id = valid_vehicles[0].id
        elif not valid_vehicles:
            issues.append(
                _clarification(
                    "transport.vehicle",
                    "Автобус",
                    "Добавьте доступный автобус с действующей диагностической картой в профиль.",
                    reason="no_available_vehicle",
                )
            )
        else:
            issues.append(
                _clarification(
                    "transport.vehicle",
                    "Автобус",
                    "Выберите автобус для этой поездки из профиля перевозчика.",
                    reason="vehicle_selection_required",
                )
            )

    drivers = list(
        (
            await db.scalars(
                select(Driver)
                .where(
                    Driver.tenant_id == trip.tenant_id,
                    Driver.is_active.is_(True),
                )
                .order_by(Driver.full_name)
            )
        ).all()
    )
    if trip.selected_driver_id is not None and not any(
        driver.id == trip.selected_driver_id for driver in drivers
    ):
        trip.selected_driver_id = None
        issues.append(
            _clarification(
                "transport.driver",
                "Водитель",
                "Ранее выбранный водитель больше не активен. Выберите другого водителя.",
                reason="selected_driver_became_unavailable",
            )
        )
    requested_driver = _clean_optional(preferences.get("driver_name"))
    if requested_driver:
        normalized = requested_driver.casefold()
        exact = [
            driver
            for driver in drivers
            if driver.full_name.casefold() == normalized
        ]
        contains = [
            driver
            for driver in drivers
            if normalized in driver.full_name.casefold()
            or driver.full_name.casefold() in normalized
        ]
        match = exact[0] if len(exact) == 1 else (
            contains[0] if len(contains) == 1 else None
        )
        if match is None:
            issues.append(
                _clarification(
                    "transport.driver",
                    "Водитель",
                    (
                        f"Водитель {requested_driver} не найден однозначно среди "
                        "активных водителей профиля. Выберите водителя в профиле."
                    ),
                    reason="requested_driver_unavailable",
                )
            )
        else:
            trip.selected_driver_id = match.id
            issues = [
                item for item in issues if item["field"] != "transport.driver"
            ]
    elif trip.selected_driver_id is None:
        if len(drivers) == 1:
            trip.selected_driver_id = drivers[0].id
        elif not drivers:
            issues.append(
                _clarification(
                    "transport.driver",
                    "Водитель",
                    "Добавьте активного водителя категории D в профиль.",
                    reason="no_available_driver",
                )
            )
        else:
            issues.append(
                _clarification(
                    "transport.driver",
                    "Водитель",
                    "Выберите водителя для этой поездки из профиля перевозчика.",
                    reason="driver_selection_required",
                )
            )

    children_count = len(canonical.get("children") or []) or int(
        (canonical.get("trip") or {}).get("children_count") or 0
    )
    companions_count = len(canonical.get("companions") or []) or int(
        (canonical.get("trip") or {}).get("companions_count") or 0
    )
    passengers = children_count + companions_count
    largest_capacity = max((vehicle.seats for vehicle in valid_vehicles), default=0)
    multi_bus_required = children_count > 55 or passengers > largest_capacity
    if multi_bus_required:
        minimum_count = max(2, (children_count + 54) // 55)
        selected: list[Vehicle] = []
        capacity = 0
        for vehicle in sorted(valid_vehicles, key=lambda item: item.seats, reverse=True):
            selected.append(vehicle)
            capacity += vehicle.seats
            if len(selected) >= minimum_count and capacity >= passengers:
                break
        if len(selected) < minimum_count or capacity < passengers:
            issues = [
                item for item in issues if item["field"] != "transport.vehicle"
            ]
            issues.append(
                _clarification(
                    "transport.capacity",
                    "Вместимость автобусов",
                    (
                        f"Для {children_count} детей и {companions_count} "
                        "сопровождающих не хватает доступных мест. Добавьте "
                        "автобусы с действующими диагностическими картами."
                    ),
                    reason="insufficient_fleet_capacity",
                )
            )
        elif len(drivers) < len(selected):
            issues = [
                item for item in issues if item["field"] != "transport.driver"
            ]
            issues.append(
                _clarification(
                    "transport.driver_count",
                    "Количество водителей",
                    f"Для поездки требуется {len(selected)} автобуса и столько же водителей.",
                    reason="insufficient_driver_count",
                )
            )
        else:
            assigned_drivers = drivers[: len(selected)]
            canonical["transport_assignments"] = {
                "vehicle_ids": [item.id for item in selected],
                "driver_ids": [item.id for item in assigned_drivers],
                "passenger_capacity": capacity,
                "required_vehicle_count": len(selected),
            }
            trip.selected_vehicle_id = selected[0].id
            trip.selected_driver_id = assigned_drivers[0].id
            issues = [
                item
                for item in issues
                if item["field"] not in {"transport.vehicle", "transport.driver"}
            ]
    return issues


def _clarification(
    field: str,
    label: str,
    question: str,
    *,
    reason: str,
    suggested_value: str | None = None,
) -> dict[str, Any]:
    return {
        "field": field,
        "label": label,
        "question": question,
        "suggested_value": suggested_value,
        "reason": reason,
    }


def _missing_clarifications(
    canonical: dict[str, Any],
    *,
    transport_issues: list[dict[str, Any]],
    selected_vehicle_id: str | None,
    selected_driver_id: str | None,
) -> list[dict[str, Any]]:
    customer = canonical.get("customer") or {}
    trip = canonical.get("trip") or {}
    route = canonical.get("route") or {}
    children = canonical.get("children") or []
    companions = canonical.get("companions") or []
    questions: list[dict[str, Any]] = []

    required = [
        ("customer.type", "Тип заказчика", customer.get("type")),
        ("customer.name", "Заказчик", customer.get("name")),
        ("customer.address", "Адрес заказчика", customer.get("address")),
        ("customer.phone", "Телефон заказчика", customer.get("phone")),
        ("trip.trip_date", "Дата поездки", trip.get("trip_date")),
        ("trip.start_time", "Время начала", trip.get("start_time")),
        ("route.text", "Маршрут", route.get("text")),
        ("route.region_codes", "Регионы маршрута", route.get("region_codes")),
        ("route.address_start", "Адрес отправления", route.get("address_start")),
        ("route.destination", "Пункт назначения", route.get("destination")),
    ]
    prompts = {
        "customer.type": "Уточните тип заказчика: юридическое лицо, ИП или физическое лицо.",
        "customer.name": "Укажите полное наименование или ФИО заказчика.",
        "customer.address": "Укажите адрес заказчика.",
        "customer.phone": "Укажите телефон заказчика.",
        "trip.trip_date": "Укажите дату поездки.",
        "trip.start_time": "Укажите время начала поездки.",
        "route.text": "Опишите маршрут поездки: отправление, остановки и пункт назначения.",
        "route.region_codes": "Укажите регионы маршрута, например 78 и 47.",
        "route.address_start": "Укажите точный адрес отправления.",
        "route.destination": "Укажите точный пункт назначения.",
    }
    for field, label, value in required:
        if value in (None, "", []):
            questions.append(
                _clarification(
                    field,
                    label,
                    prompts[field],
                    reason="required_field_missing",
                )
            )

    if customer.get("type") in {"legal", "ip"} and not customer.get("inn"):
        questions.append(
            _clarification(
                "customer.inn",
                "ИНН заказчика",
                "Укажите ИНН организации-заказчика или ИП.",
                reason="legal_customer_inn_required",
            )
        )
    if not trip.get("end_time"):
        questions.append(
            _clarification(
                "trip.end_time",
                "Время окончания",
                "Не нашёл время окончания поездки. Указать 18:00 или другое время?",
                reason="end_time_required",
                suggested_value="18:00",
            )
        )
    children_count = len(children) or int(trip.get("children_count") or 0)
    if (
        0 < children_count < 8
        and not trip.get("small_group_notification_confirmed")
    ):
        questions.append(
            _clarification(
                "trip.small_group_override",
                "Группа меньше 8 детей",
                (
                    f"В заявке {children_count} детей. Для такой группы "
                    "уведомление обычно не требуется. Не создавать его? "
                    "Если уведомление всё же нужно, явно подтвердите продолжение."
                ),
                reason="small_group_notification_not_recommended",
            )
        )
    adult_participants = [
        item
        for item in companions
        if re.search(
            r"(?iu)совершеннолетн\w*\s+участник",
            str(item.get("role") or ""),
        )
    ]
    if not children and adult_participants:
        questions.append(
            _clarification(
                "children",
                "В списке нет несовершеннолетних",
                (
                    f"На дату поездки все {len(adult_participants)} участников "
                    "уже совершеннолетние. Уведомление об организованной "
                    "перевозке группы детей создавать не нужно. Проверьте дату "
                    "поездки и списки."
                ),
                reason="no_minor_children_notification_not_required",
            )
        )
    elif not children:
        questions.append(
            _clarification(
                "children",
                "Список детей",
                "Не найден полный список детей. Прикрепите его или укажите данные детей.",
                reason="children_required",
            )
        )
    elif any(
        not item.get("full_name") or not item.get("birth_date")
        for item in children
    ):
        questions.append(
            _clarification(
                "children",
                "Список детей",
                "Для части детей не хватает ФИО или даты рождения. Уточните список.",
                reason="children_incomplete",
            )
        )
    if not companions:
        questions.append(
            _clarification(
                "companions",
                "Сопровождающие",
                "Не найден список сопровождающих. Укажите хотя бы одного сопровождающего.",
                reason="companions_required",
            )
        )
    if selected_vehicle_id is None and not any(
        item["field"] == "transport.vehicle" for item in transport_issues
    ):
        transport_issues.append(
            _clarification(
                "transport.vehicle",
                "Автобус",
                "Выберите автобус из профиля перевозчика.",
                reason="vehicle_selection_required",
            )
        )
    if selected_driver_id is None and not any(
        item["field"] == "transport.driver" for item in transport_issues
    ):
        transport_issues.append(
            _clarification(
                "transport.driver",
                "Водитель",
                "Выберите водителя из профиля перевозчика.",
                reason="driver_selection_required",
            )
        )
    questions.extend(transport_issues)

    uncertain = set(canonical.get("uncertain_fields") or [])
    existing_fields = {item["field"] for item in questions}
    for field in sorted(uncertain):
        if field not in existing_fields and field not in SYSTEM_DERIVED_FIELDS:
            questions.append(
                _clarification(
                    field,
                    "Проверка данных",
                    f"Подтвердите распознанное значение поля «{field}» или исправьте его.",
                    reason="low_confidence",
                )
            )
    return questions


def _apply_canonical_to_trip(trip: Trip, canonical: dict[str, Any]) -> None:
    customer = canonical.get("customer") or {}
    trip_data = canonical.get("trip") or {}
    route = canonical.get("route") or {}
    if customer.get("type"):
        trip.customer_type = customer["type"]
    if customer.get("name"):
        trip.customer_name = customer["name"]
    if customer.get("inn"):
        trip.customer_inn = customer["inn"]
    if customer.get("address"):
        trip.customer_address = customer["address"]
    parsed_date = _as_date(trip_data.get("trip_date"))
    if parsed_date:
        trip.trip_date = parsed_date
    if trip_data.get("start_time"):
        trip.start_time = trip_data["start_time"]
    if trip_data.get("end_time"):
        trip.end_time = trip_data["end_time"]
    if route.get("text"):
        trip.route_text = route["text"]
    if trip_data.get("children_count") is not None:
        trip.passenger_count = int(trip_data["children_count"])


def apply_manual_trip_updates(trip: Trip, values: dict[str, Any]) -> None:
    """Keep workflow slots/questions consistent with an authorized PATCH."""

    if not values or not isinstance(trip.extracted_data, dict):
        return
    canonical = copy.deepcopy(trip.extracted_data)
    if canonical.get("schema_version") != 1:
        return
    customer = canonical.setdefault("customer", {})
    trip_data = canonical.setdefault("trip", {})
    route = canonical.setdefault("route", {})
    slot_states = canonical.setdefault("slot_states", {})
    resolved_paths: set[str] = set()

    field_paths = {
        "customer_type": ("customer.type", customer, "type"),
        "customer_name": ("customer.name", customer, "name"),
        "customer_inn": ("customer.inn", customer, "inn"),
        "customer_address": ("customer.address", customer, "address"),
        "trip_date": ("trip.trip_date", trip_data, "trip_date"),
        "start_time": ("trip.start_time", trip_data, "start_time"),
        "end_time": ("trip.end_time", trip_data, "end_time"),
        "route_text": ("route.text", route, "text"),
        "passenger_count": (
            "trip.children_count",
            trip_data,
            "children_count",
        ),
    }
    for api_field, (path, target, key) in field_paths.items():
        if api_field not in values or values[api_field] in (None, ""):
            continue
        value = values[api_field]
        if isinstance(value, date):
            value = value.isoformat()
        target[key] = value
        resolved_paths.add(path)
        slot_states[path] = {
            "status": "confirmed",
            "source": "user",
            "confidence": 1.0,
            "revision": (canonical.get("workflow") or {}).get("revision", 0),
        }
    if "route_text" in values and values["route_text"]:
        route["approximate"] = False
    for api_field, path in (
        ("selected_vehicle_id", "transport.vehicle"),
        ("selected_driver_id", "transport.driver"),
    ):
        if api_field in values and values[api_field]:
            resolved_paths.add(path)
            slot_states[path] = {
                "status": "confirmed",
                "source": "user",
                "confidence": 1.0,
                "revision": (canonical.get("workflow") or {}).get(
                    "revision", 0
                ),
            }

    if not resolved_paths:
        return
    canonical["missing_clarifications"] = [
        item
        for item in canonical.get("missing_clarifications", [])
        if not isinstance(item, dict) or item.get("field") not in resolved_paths
    ]
    canonical["uncertain_fields"] = [
        field
        for field in canonical.get("uncertain_fields", [])
        if field not in resolved_paths
    ]
    trip.extracted_data = canonical
    if (
        not canonical["missing_clarifications"]
        and not canonical["uncertain_fields"]
        and trip.status in {"clarifying", "draft"}
    ):
        trip.status = "needs_review"
        trip.progress = max(trip.progress, 35)


def _summary(trip: Trip, canonical: dict[str, Any]) -> str:
    customer = canonical.get("customer") or {}
    trip_data = canonical.get("trip") or {}
    route = canonical.get("route") or {}
    parts = [
        f"Заказчик: {customer.get('name') or 'нужно уточнить'}",
        f"Дата: {trip_data.get('trip_date') or 'нужно уточнить'}",
        (
            "Время: "
            f"{trip_data.get('start_time') or 'нужно уточнить'}–"
            f"{trip_data.get('end_time') or 'нужно уточнить'}"
        ),
        f"Маршрут: {route.get('text') or 'нужно уточнить'}",
        f"Детей: {trip_data.get('children_count') or 0}",
        f"Сопровождающих: {trip_data.get('companions_count') or 0}",
    ]
    if canonical.get("ignored_carrier_mentions"):
        parts.append(
            "Упомянутый во входных документах перевозчик проигнорирован; "
            "используется перевозчик из профиля."
        )
    return "\n".join(parts)


async def _mark_run_failure(
    db: AsyncSession,
    *,
    run: TripWorkflowRun,
    trip: Trip,
    user: User,
    code: str,
    message: str,
    config_required: bool = False,
) -> None:
    run.status = "config_required" if config_required else "failed"
    run.error_code = code
    run.error_message = message[:2000]
    run.finished_at = utcnow()
    if config_required:
        trip.status = "draft"
        trip.progress = 0
    else:
        trip.status = "failed"
    assistant = Message(
        tenant_id=trip.tenant_id,
        conversation_id=trip.conversation_id,
        author_id=None,
        role="assistant",
        content=message,
        metadata_json={
            "message_type": "system_status",
            "status": run.status,
            "workflow_run_id": run.id,
            "configuration_required": config_required,
        },
    )
    db.add(assistant)
    add_audit_event(
        db,
        user=user,
        action="trip.extraction_failed",
        entity_type="trip_workflow_run",
        entity_id=run.id,
        details={"trip_id": trip.id, "error_code": code},
    )


async def run_trip_workflow(app: FastAPI, run_id: str) -> None:
    """Persisted worker entry point."""

    async with app.state.session_factory() as db:
        run = await db.get(TripWorkflowRun, run_id)
        if run is None or run.status != "queued":
            return
        trip = await db.scalar(
            select(Trip).where(
                Trip.id == run.trip_id,
                Trip.tenant_id == run.tenant_id,
            )
        )
        user = await db.scalar(
            select(User).where(
                User.id == run.triggered_by_id,
                User.tenant_id == run.tenant_id,
            )
        )
        if trip is None or user is None:
            return
        latest_revision = await db.scalar(
            select(func.max(TripWorkflowRun.revision)).where(
                TripWorkflowRun.trip_id == trip.id
            )
        )
        if latest_revision != run.revision:
            run.status = "superseded"
            run.finished_at = utcnow()
            await db.commit()
            return
        if not _provider_is_configured(app):
            await _mark_run_failure(
                db,
                run=run,
                trip=trip,
                user=user,
                code="llm_config_required",
                message=_configuration_message(app.state.settings),
                config_required=True,
            )
            await db.commit()
            return

        run.status = "extracting"
        run.started_at = utcnow()
        trip.status = "extracting"
        trip.progress = max(trip.progress, 15)
        await db.commit()

        try:
            async with _provider_for_app(app) as (
                extraction_provider,
                image_extractor,
            ):
                documents = await asyncio.wait_for(
                    _documents_for_run(
                        db,
                        app=app,
                        trip=trip,
                        image_extractor=image_extractor,
                        native_pdf=bool(
                            getattr(
                                extraction_provider,
                                "supports_native_pdf",
                                False,
                            )
                        ),
                    ),
                    timeout=app.state.settings.llm_workflow_timeout_seconds,
                )
                trigger_message = (
                    await db.scalar(
                        select(Message).where(
                            Message.id == run.message_id,
                            Message.tenant_id == run.tenant_id,
                        )
                    )
                    if run.message_id
                    else None
                )
                message_query = (
                    select(Message)
                    .where(
                        Message.tenant_id == trip.tenant_id,
                        Message.conversation_id == trip.conversation_id,
                        Message.role == "user",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(12)
                )
                if trigger_message is not None:
                    message_query = message_query.where(
                        Message.created_at <= trigger_message.created_at
                    )
                messages = list((await db.scalars(message_query)).all())
                messages.reverse()
                context = TripExtractionContextDTO(
                    current_fields=trip.extracted_data or {},
                    carrier_profile_identity=(
                        {
                            "legal_name": carrier_profile.legal_name,
                            "short_name": carrier_profile.short_name,
                            "inn": carrier_profile.inn,
                        }
                        if (
                            carrier_profile := await db.scalar(
                                select(CarrierProfile).where(
                                    CarrierProfile.tenant_id == trip.tenant_id
                                )
                            )
                        )
                        else {}
                    ),
                    latest_user_message=(
                        trigger_message.content if trigger_message else ""
                    ),
                    recent_user_messages=_budget_recent_messages(
                        [
                            item.content
                            for item in messages
                            if item.content
                            and (
                                trigger_message is None
                                or item.id != trigger_message.id
                            )
                        ],
                        total_budget=(
                            app.state.settings.llm_max_recent_message_chars
                        ),
                    ),
                    documents=documents,
                )
                result = await asyncio.wait_for(
                    extraction_provider.extract_trip(context),
                    timeout=app.state.settings.llm_workflow_timeout_seconds,
                )

            llm_metadata = {
                "provider": result.provider,
                "requested_model": result.requested_model,
                "actual_model": result.actual_model,
                "response_id": result.response_id,
                "attempt_count": result.attempt_count,
            }
            document_text = "\n".join(document.text for document in documents)
            normalize_extracted_passenger_roles(
                result.data,
                source_text=document_text,
            )
            route_memory_match = enrich_extracted_route_from_memory(result.data)
            derive_missing_transfer_end_time(
                result.data,
                source_text=document_text,
                scheduling_hint=context.latest_user_message,
            )
            if route_memory_match:
                result.data.confirmed_fields.extend(
                    field
                    for field in ("route.distance", "route.duration", "route.roads")
                    if field not in result.data.confirmed_fields
                )
            canonical = _canonical_from_result(
                current=trip.extracted_data or {},
                extraction=result.data,
                revision=run.revision,
                source_documents=documents,
                llm_metadata=llm_metadata,
            )
            await _apply_system_derived_fields(
                db,
                trip=trip,
                canonical=canonical,
            )
            _apply_canonical_to_trip(trip, canonical)
            transport_issues = await _apply_transport_preferences(
                db, trip=trip, canonical=canonical
            )
            missing = _missing_clarifications(
                canonical,
                transport_issues=transport_issues,
                selected_vehicle_id=trip.selected_vehicle_id,
                selected_driver_id=trip.selected_driver_id,
            )
            canonical["missing_clarifications"] = missing
            canonical["workflow"] = {
                "run_id": run.id,
                "revision": run.revision,
                "status": "clarifying" if missing else "needs_review",
            }
            summary = _summary(trip, canonical)
            trip.extracted_data = canonical
            trip.status = "clarifying" if missing else "needs_review"
            trip.progress = 25 if missing else 35

            run.status = trip.status
            run.actual_provider = result.provider
            run.actual_model = result.actual_model
            run.response_id = result.response_id
            run.attempt_count = result.attempt_count
            run.result_json = {
                "missing_clarifications": missing,
                "uncertain_fields": canonical["uncertain_fields"],
                "summary": summary,
                "can_confirm": not missing,
                "source_document_count": len(documents),
            }
            run.finished_at = utcnow()
            content = (
                "\n".join(
                    [
                        "Я сохранил распознанные данные. Нужно уточнить:",
                        *[
                            f"{index}. {item['question']}"
                            for index, item in enumerate(missing, start=1)
                        ],
                    ]
                )
                if missing
                else (
                    "Данные распознаны. Проверьте сводку и подтвердите заявку:\n"
                    + summary
                )
            )
            db.add(
                Message(
                    tenant_id=trip.tenant_id,
                    conversation_id=trip.conversation_id,
                    author_id=None,
                    role="assistant",
                    content=content,
                    metadata_json={
                        "message_type": (
                            "assistant_question"
                            if missing
                            else "assistant_summary"
                        ),
                        "workflow_run_id": run.id,
                        "trip_id": trip.id,
                        "status": trip.status,
                        "missing_clarifications": missing,
                        "can_confirm": not missing,
                    },
                )
            )
            add_audit_event(
                db,
                user=user,
                action="trip.extraction_completed",
                entity_type="trip_workflow_run",
                entity_id=run.id,
                details={
                    "trip_id": trip.id,
                    "status": trip.status,
                    "actual_provider": result.provider,
                    "actual_model": result.actual_model,
                    "missing_fields": [item["field"] for item in missing],
                    "uncertain_fields": canonical["uncertain_fields"],
                    "ignored_carrier_mention_count": len(
                        canonical["ignored_carrier_mentions"]
                    ),
                    "llm_truncated_document_count": sum(
                        any(
                            warning.startswith("llm_text_truncated:")
                            for warning in document.warnings
                        )
                        for document in documents
                    ),
                },
            )
            await db.commit()
        except asyncio.TimeoutError:
            await _mark_run_failure(
                db,
                run=run,
                trip=trip,
                user=user,
                code="llm_timeout",
                message=(
                    "Распознавание превысило допустимое время. "
                    "Данные сохранены; можно безопасно повторить попытку."
                ),
            )
            await db.commit()
        except (GeminiError, OpenRouterError) as exc:
            text = str(exc)
            auth_error = "HTTP 401" in text or "HTTP 403" in text
            provider_name = app.state.settings.llm_provider.strip().lower()
            provider_title = (
                "Gemini" if provider_name == "gemini" else "OpenRouter"
            )
            key_name = (
                "GEMINI_API_KEY"
                if provider_name == "gemini"
                else "OPENROUTER_API_KEY"
            )
            await _mark_run_failure(
                db,
                run=run,
                trip=trip,
                user=user,
                code=(
                    "llm_config_required"
                    if auth_error
                    else "llm_provider_error"
                ),
                message=(
                    f"{provider_title} отклонил API-ключ. Проверьте "
                    f"{key_name} в .env и перезапустите API."
                    if auth_error
                    else (
                        f"{provider_title} временно не смог обработать заявку. "
                        "Данные сохранены; запустите разбор повторно."
                    )
                ),
                config_required=auth_error,
            )
            await db.commit()
        except Exception:
            await _mark_run_failure(
                db,
                run=run,
                trip=trip,
                user=user,
                code="extraction_error",
                message=(
                    "Не удалось завершить автоматический разбор. "
                    "Данные сохранены; можно безопасно повторить попытку."
                ),
            )
            await db.commit()


def dispatch_trip_workflow(
    app: FastAPI,
    run_id: str,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> asyncio.Task | None:
    """Queue a run in Celery, with an in-process test/local fallback."""

    settings: Settings = app.state.settings
    injected = getattr(app.state, "trip_extraction_provider", None)
    in_process = injected is not None or settings.app_env.lower() == "test"
    if not in_process:
        try:
            from ..worker import celery_app

            celery_app.send_task(
                "automation.process_trip_workflow",
                args=[run_id],
            )
            return None
        except Exception:
            # A non-Docker local launch may not have Redis. The persisted run
            # remains idempotent, so the compatibility fallback is safe.
            pass
    if background_tasks is not None:
        background_tasks.add_task(run_trip_workflow, app, run_id)
        return None
    return asyncio.create_task(
        run_trip_workflow(app, run_id),
        name=f"buscript-trip-workflow-{run_id}",
    )


async def recover_pending_workflow_runs(app: FastAPI) -> list[asyncio.Task]:
    """Resume persisted local runs that were interrupted by an API restart."""

    async with app.state.session_factory() as db:
        runs = list(
            (
                await db.scalars(
                    select(TripWorkflowRun)
                    .where(
                        TripWorkflowRun.status.in_({"queued", "extracting"})
                    )
                    .order_by(TripWorkflowRun.created_at)
                )
            ).all()
        )
        for run in runs:
            run.status = "queued"
            run.started_at = None
            run.error_code = None
            run.error_message = None
        if runs:
            await db.commit()
    tasks = [
        dispatch_trip_workflow(app, run.id)
        for run in runs
    ]
    return [task for task in tasks if task is not None]


def workflow_state_payload(
    *,
    trip: Trip,
    run: TripWorkflowRun | None,
) -> dict[str, Any]:
    extracted = trip.extracted_data or {}
    result = run.result_json if run else {}
    missing = extracted.get(
        "missing_clarifications",
        result.get("missing_clarifications", []),
    )
    uncertain = extracted.get(
        "uncertain_fields", result.get("uncertain_fields", [])
    )
    return {
        "trip": TripRead.model_validate(trip).model_dump(mode="json"),
        "run": (
            {
                "id": run.id,
                "status": run.status,
                "revision": run.revision,
                "requested_provider": run.requested_provider,
                "requested_model": run.requested_model,
                "provider": run.actual_provider,
                "model": run.actual_model,
                "error_code": run.error_code,
                "error_message": run.error_message,
                "created_at": run.created_at,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
            }
            if run
            else None
        ),
        "extracted_fields": {
            key: value
            for key, value in extracted.items()
            if key
            in {
                "customer",
                "trip",
                "route",
                "children",
                "companions",
                "transport_preferences",
                "slot_states",
                "ignored_carrier_mentions",
            }
        },
        "missing_clarifications": missing,
        "uncertain_fields": uncertain,
        "summary": result.get("summary"),
        "configuration_required": bool(
            run and run.status == "config_required"
        ),
        "can_confirm": bool(
            not missing
            and not uncertain
            and run
            and run.status not in {
                "queued",
                "extracting",
                "config_required",
                "failed",
            }
        ),
    }


async def latest_workflow_run(
    db: AsyncSession, *, trip_id: str, tenant_id: str
) -> TripWorkflowRun | None:
    return await db.scalar(
        select(TripWorkflowRun)
        .where(
            TripWorkflowRun.trip_id == trip_id,
            TripWorkflowRun.tenant_id == tenant_id,
        )
        .order_by(TripWorkflowRun.revision.desc())
        .limit(1)
    )
