"""Typed intent routing and profile-data imports for the universal chat."""

from __future__ import annotations

import base64
import re
from collections import defaultdict
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Driver, Vehicle
from .gemini import GeminiProvider
from .schemas import StrictDTO, StructuredLLMResultDTO


class ChatIntent(StrEnum):
    TRIP_REQUEST = "trip_request"
    TRIP_CLARIFICATION = "trip_clarification"
    BATCH_TRIP_REQUEST = "batch_trip_request"
    FLEET_UPDATE = "fleet_update"
    PROFILE_UPDATE = "profile_update"
    GENERAL_QUESTION = "general_question"
    UNKNOWN = "unknown"


class ImportedVehicleDTO(StrictDTO):
    registration_plate: str = Field(min_length=5, max_length=16)
    make_model: str | None = Field(default=None, max_length=255)
    year: int | None = Field(default=None, ge=1950, le=2100)
    seats: int | None = Field(default=None, ge=1, le=120)
    diagnostic_card_number: str | None = Field(default=None, max_length=64)
    diagnostic_card_valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ImportedDriverDTO(StrictDTO):
    full_name: str = Field(min_length=2, max_length=255)
    license_number: str = Field(min_length=3, max_length=64)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=32)
    license_categories: str = Field(default="D", min_length=1, max_length=64)
    license_issued_at: date | None = None
    experience_from: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class BatchTripGroupDTO(StrictDTO):
    title: str = Field(min_length=1, max_length=255)
    source_file_names: list[str] = Field(min_length=1)
    source_page_ranges: dict[str, list[int]] = Field(default_factory=dict)
    source_sheet_names: dict[str, list[str]] = Field(default_factory=dict)
    extraction_hint: str = Field(min_length=1, max_length=4000)


class ChatIntentResultDTO(StrictDTO):
    intent: ChatIntent
    vehicles: list[ImportedVehicleDTO] = Field(default_factory=list)
    drivers: list[ImportedDriverDTO] = Field(default_factory=list)
    trip_groups: list[BatchTripGroupDTO] = Field(default_factory=list)
    answer: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


CHAT_ROUTER_PROMPT = """\
Ты — маршрутизатор универсального чата Buscript. Определи намерение
по тексту и вложениям. Не считай любой файл заявкой на поездку.

- fleet_update: список автобусов, водителей, госномеров, удостоверений или
  команда добавить/обновить их в профиле. Сам список без каких-либо пояснений
  тоже означает fleet_update. Извлеки все строки в vehicles/drivers.
- trip_request: конкретная заявка на перевозку с датой, маршрутом, детьми или
  заказчиком.
- batch_trip_request: во вложениях несколько самостоятельных заявок. Раздели
  их в trip_groups, сопоставив каждой заявке только её файлы (сама заявка,
  список детей и сопровождающих). Не объединяй разные даты/маршруты/группы.
  Для каждого элемента дай короткий extraction_hint с признаками связи.
  Разные даты или обратные направления — отдельные заявки, даже если используют
  один список детей. Например поездка туда 03.08 и обратно 16.08 — две группы.
  Договор, приложение и файл с реквизитами заказчика можно включить сразу в
  несколько групп, если они относятся к общей стороне этих поездок.
  Если один PDF содержит несколько заявок, один и тот же файл может входить в
  несколько групп. Обязательно укажи для каждой группы source_page_ranges:
  имя файла -> [первая страница, последняя страница], нумерация с 1.
  Несколько заявок могут находиться даже на одной странице. В таком случае
  создай отдельную trip_group для каждой даты/поездки, повтори одинаковый
  диапазон страниц (например [1,1]) и сделай extraction_hint однозначным:
  укажи дату, время, маршрут, заказчика и иные отличительные признаки.
  Если на одну поездку заказано N автобусов, создай N trip_groups: порталу нужно
  отдельное уведомление на каждый автобус и водителя. Даже при одинаковом времени
  это разные группы («автобус 1», «автобус 2» и т. д.). В extraction_hint явно укажи номер
  автобуса и его время подачи. Не объединяй четыре автобуса в одну заявку.
  Но не путай это с несколькими плечами одного автобуса: если в один день одна и та же
  группа едет A–B, а позже B–A, это одно уведомление A–B–A с start_time первого плеча и
  end_time последнего. Объедини такие строки в одну trip_group на каждую дату и группу.
  Диапазон дат вида «28–31 июля» означает отдельную поездку в каждый день: создай группы
  на 28, 29, 30 и 31 июля, а не одну группу «28–31».
  Числа в скобках после маршрута (например 2013, 2014, 2015) могут быть годом рождения/кодом
  группы, а НЕ годом поездки. Год поездки бери из датированной заявки/списка; например список с
  периодом 27.07.2026–01.08.2026 задаёт 2026 год для связанного июльского расписания.
  Если к расписанию приложены списки только для определённых групп/периода, создавай заявки только для
  строк, которые можно связать с этими списками. Не создавай старые/несвязанные строки других команд.
  При конфликте года список пассажиров имеет меньший приоритет, чем договор,
  заявка, приложение или расчёт стоимости. Заказчик — заказчик/принципал/
  плательщик из формального документа, а не учреждение из заголовка списка.
  Определяй роль по обязанностям, а не только словам «Фрахтователь» и
  «Фрахтовщик»: сторона, предоставляющая автобус, — исполнитель; сторона,
  оплачивающая и управляющая заказом, — заказчик.
  В агентской схеме не подменяй Агента Принципалом: если Агент оформляет
  транспорт у перевозчика, все связанные поездки должны иметь одного и того же
  заказчика-Агента. Учреждение в заголовке списка пассажиров заказчиком от этого
  не становится.
- trip_clarification: ответ на вопрос по уже созданной поездке.
- profile_update: реквизиты самого перевозчика, не автобусы/водители.
- general_question: вопрос о системе без изменения данных. Дай краткий answer.
Не выдумывай поля. Госномера нормализуй без пробелов.
"""


_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def _deterministic_timetable_groups(
    documents: list[tuple[str, str, str, str | None]],
) -> list[BatchTripGroupDTO]:
    """Expand a dated timetable and bind each row to its passenger list."""

    passenger_lists: dict[str, str] = {}
    anchors: list[tuple[date, date]] = []
    for name, _media_type, text, _inline_data in documents:
        group_match = re.search(
            r"(?iu)(20\d{2})\s+года\s+рождения",
            text[:4000],
        )
        if group_match:
            passenger_lists[group_match.group(1)] = name
        for match in re.finditer(
            r"\b(\d{2})[.]?(\d{2})[.]?(20\d{2})\s*[-–—]\s*"
            r"(\d{2})[.]?(\d{2})[.]?(20\d{2})\b",
            text[:5000],
        ):
            try:
                anchors.append(
                    (
                        date(int(match.group(3)), int(match.group(2)), int(match.group(1))),
                        date(int(match.group(6)), int(match.group(5)), int(match.group(4))),
                    )
                )
            except ValueError:
                continue
    if not passenger_lists or not anchors:
        return []
    window_start = min(item[0] for item in anchors)
    window_end = max(item[1] for item in anchors)

    schedule: tuple[str, str] | None = None
    row_pattern = re.compile(r"^\s*(\d{1,2}:\d{2})\s+(.+?)\s*\((20\d{2})\)\s*$")
    for name, _media_type, text, _inline_data in documents:
        if sum(bool(row_pattern.match(line)) for line in text.splitlines()) >= 3:
            schedule = (name, text)
            break
    if schedule is None:
        return []

    rows: dict[tuple[date, str], list[tuple[str, str]]] = defaultdict(list)
    active_dates: list[date] = []
    heading_pattern = re.compile(
        r"^\s*(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\s+([а-яё]+)\s*$",
        re.IGNORECASE,
    )
    for raw_line in schedule[1].splitlines():
        line = " ".join(raw_line.split())
        heading = heading_pattern.match(line)
        if heading and heading.group(3).casefold() in _MONTHS:
            month = _MONTHS[heading.group(3).casefold()]
            first = int(heading.group(1))
            last = int(heading.group(2) or first)
            active_dates = []
            for day in range(first, last + 1):
                try:
                    candidate = date(window_start.year, month, day)
                except ValueError:
                    continue
                if window_start <= candidate <= window_end:
                    active_dates.append(candidate)
            continue
        row = row_pattern.match(line)
        if row is None or not active_dates or row.group(3) not in passenger_lists:
            continue
        for trip_date in active_dates:
            rows[(trip_date, row.group(3))].append((row.group(1), row.group(2)))

    result: list[BatchTripGroupDTO] = []
    for (trip_date, group), legs in sorted(rows.items()):
        if not legs:
            continue
        timeline = "; ".join(f"{time} {route}" for time, route in legs)
        result.append(
            BatchTripGroupDTO(
                title=f"{trip_date:%d.%m.%Y} — группа {group}",
                source_file_names=[schedule[0], passenger_lists[group]],
                extraction_hint=(
                    f"Одно уведомление на {trip_date:%d.%m.%Y}, группа {group} года рождения. "
                    f"Все плечи этой группы за день объедини в один маршрут: {timeline}. "
                    f"Начало — {legs[0][0]}; окончание рассчитай как время прибытия после последнего плеча, "
                    f"которое отправляется в {legs[-1][0]}. Не путай {group} с годом поездки."
                ),
            )
        )
    return result if len(result) >= 2 else []


def _deterministic_numbered_sheet_groups(
    content: str,
    documents: list[tuple[str, str, str, str | None]],
) -> list[BatchTripGroupDTO]:
    """Pair numbered detachment sheets when one notification is required per bus."""

    if not re.search(r"(?iu)(кажд\w*\s+автобус|отдельн\w*\s+уведомлен)", content):
        return []
    candidates: list[tuple[str, list[tuple[int, str]]]] = []
    marker = re.compile(r"(?imu)^===\s*Лист:\s*(\d+)\s+отряд\s*===\s*$")
    for name, _media_type, text, _inline_data in documents:
        sheets = [(int(match.group(1)), f"{match.group(1)} отряд") for match in marker.finditer(text)]
        if len(sheets) >= 4 and len(sheets) % 2 == 0:
            candidates.append((name, sorted(sheets)))
    if len(candidates) != 1:
        return []

    workbook_name, sheets = candidates[0]
    source_names = [name for name, *_rest in documents]
    filename_text = "\n".join(name for name, *_rest in documents)
    combined_text = "\n".join(text[:8000] for _name, _media_type, text, _inline in documents)
    declared_bus_counts = [
        int(value)
        for value in re.findall(r"(?iu)\b(\d{1,3})\s+автобус(?:а|ов)?\b", combined_text)
    ]
    expected_groups = len(sheets) // 2
    if declared_bus_counts and expected_groups not in declared_bus_counts:
        return []
    date_match = re.search(r"\b(\d{2}[.]\d{2}[.]20\d{2})\b", filename_text)
    if date_match is None:
        date_match = re.search(
            r"(?iu)(?:дата\s+(?:поездки|подачи)|дата)[^\n]{0,80}?"
            r"(\d{2}[.]\d{2}[.]20\d{2})",
            combined_text,
        )
    date_label = date_match.group(1) if date_match else "по заявке"
    result: list[BatchTripGroupDTO] = []
    for index in range(0, len(sheets), 2):
        bus_number = index // 2 + 1
        selected = [sheets[index][1], sheets[index + 1][1]]
        result.append(
            BatchTripGroupDTO(
                title=f"{date_label} — Автобус {bus_number}",
                source_file_names=source_names,
                source_sheet_names={workbook_name: selected},
                extraction_hint=(
                    f"Отдельное уведомление для автобуса {bus_number}. "
                    f"Из книги пассажиров используй только листы «{selected[0]}» и "
                    f"«{selected[1]}»; другие листы относятся к другим автобусам. "
                    "Данные поездки и заказчика возьми из приложенной заявки."
                ),
            )
        )
    return result


_PLATE_LATIN_TO_CYRILLIC = str.maketrans(
    "ABEKMHOPCTYX",
    "АВЕКМНОРСТУХ",
)
_REGISTRATION_PLATE = re.compile(
    r"(?iu)([АВЕКМНОРСТУХABEKMHOPCTYX])\s*(\d{3})\s*"
    r"([АВЕКМНОРСТУХABEKMHOPCTYX]{2})\s*(\d{2,3})"
)


def _parse_import_date(value: str) -> date | None:
    match = re.search(r"\b(\d{2})[.](\d{2})[.]((?:19|20)\d{2})\b", value)
    if match is None:
        return None
    try:
        return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None


def _normalized_registration_plate(value: str) -> str | None:
    match = _REGISTRATION_PLATE.search(value.upper())
    if match is None:
        return None
    return (
        match.group(1).translate(_PLATE_LATIN_TO_CYRILLIC)
        + match.group(2)
        + match.group(3).translate(_PLATE_LATIN_TO_CYRILLIC)
        + match.group(4)
    )


def _deterministic_tabular_fleet_import(
    documents: list[tuple[str, str, str, str | None]],
) -> ChatIntentResultDTO | None:
    """Parse clear vehicle/driver tables row-by-row without one huge LLM JSON."""

    combined = "\n".join(text for _name, _media, text, _inline in documents)
    has_vehicle_table = bool(
        re.search(r"(?iu)информация\s+об\s+автобус|государственн\w*\s+регистрационн\w*\s+номер", combined)
    )
    has_driver_table = bool(
        re.search(
            r"(?iu)информация[^\n]{0,80}\bводител|"
            r"номер\s+водительск\w*\s+удостоверения",
            combined,
        )
    )
    if not (has_vehicle_table or has_driver_table):
        return None

    vehicles: list[ImportedVehicleDTO] = []
    drivers: list[ImportedDriverDTO] = []
    seen_plates: set[str] = set()
    seen_licenses: set[str] = set()
    for raw_line in combined.splitlines():
        columns = [" ".join(value.split()) for value in raw_line.split("\t")]
        if (
            has_driver_table
            and drivers
            and len(columns) > 1
            and columns[0]
            and all(not value for value in columns[1:])
            and len(re.findall(r"[А-Яа-яЁёA-Za-z-]+", columns[0])) == 1
        ):
            drivers[-1].full_name = f"{drivers[-1].full_name} {columns[0]}"
            continue
        plate_match = _REGISTRATION_PLATE.search(raw_line.upper())
        if has_vehicle_table and plate_match is not None:
            plate = _normalized_registration_plate(plate_match.group(0))
            if len(columns) >= 2 and _normalized_registration_plate(columns[1]):
                make_model = columns[0]
                plate_source = columns[1]
                diagnostic_text = columns[2] if len(columns) > 2 else ""
            else:
                make_model = re.sub(
                    r"^\s*(?:\d+[.)]?\s+)?", "", raw_line[: plate_match.start()]
                ).strip(" \t:;,-")
                plate_source = plate_match.group(0)
                diagnostic_text = raw_line[plate_match.end() :].strip()
            if plate and plate not in seen_plates and len(make_model) >= 2:
                card = re.search(r"\b(\d{10,20})\b", diagnostic_text)
                notes = None
                if plate_source.strip() != plate:
                    notes = f"Исходное обозначение госномера: {plate_source.strip()}"
                vehicles.append(
                    ImportedVehicleDTO(
                        registration_plate=plate,
                        make_model=make_model,
                        diagnostic_card_number=card.group(1) if card else None,
                        diagnostic_card_valid_until=_parse_import_date(diagnostic_text),
                        notes=notes,
                    )
                )
                seen_plates.add(plate)
                continue
        driver_columns = columns if len(columns) >= 4 else []
        if not driver_columns and has_driver_table:
            row = re.match(
                r"^\s*(.+?)\s+(\d{2}[.]\d{2}[.](?:19|20)\d{2})\s+"
                r"(\d[\d\s]{7,15}?)\s+(\d{2}[.]\d{2}[.](?:19|20)\d{2})"
                r"(?:\s+(.*))?$",
                raw_line,
            )
            if row:
                driver_columns = [
                    row.group(1),
                    row.group(2),
                    row.group(3),
                    row.group(4),
                    row.group(5) or "",
                ]
        if has_driver_table and len(driver_columns) >= 4:
            birth_date = _parse_import_date(driver_columns[1])
            issued_at = _parse_import_date(driver_columns[3])
            license_number = re.sub(r"\s+", "", driver_columns[2])
            if (
                birth_date
                and issued_at
                and re.fullmatch(r"\d{8,14}", license_number)
                and len(re.findall(r"[А-Яа-яЁёA-Za-z-]+", driver_columns[0])) >= 2
                and license_number not in seen_licenses
            ):
                notes = (
                    driver_columns[4]
                    if len(driver_columns) > 4 and driver_columns[4]
                    else None
                )
                drivers.append(
                    ImportedDriverDTO(
                        full_name=driver_columns[0],
                        license_number=license_number,
                        birth_date=birth_date,
                        license_categories="D",
                        license_issued_at=issued_at,
                        notes=notes,
                    )
                )
                seen_licenses.add(license_number)
    if not vehicles and not drivers:
        return None
    return ChatIntentResultDTO(
        intent=ChatIntent.FLEET_UPDATE,
        vehicles=vehicles,
        drivers=drivers,
        confidence=1.0,
    )


async def classify_chat_message(
    provider: GeminiProvider,
    *,
    content: str,
    documents: list[tuple[str, str, str, str | None]],
) -> StructuredLLMResultDTO[ChatIntentResultDTO]:
    deterministic_fleet = _deterministic_tabular_fleet_import(documents)
    if deterministic_fleet is not None:
        return StructuredLLMResultDTO[ChatIntentResultDTO](
            data=deterministic_fleet,
            provider="deterministic-parser",
            requested_model="tabular-fleet-v1",
            actual_model="tabular-fleet-v1",
            attempt_count=1,
        )
    parts: list[dict[str, Any]] = []
    manifest: list[str] = []
    for name, media_type, text, inline_data in documents:
        manifest.append(f"Файл {name}:\n{text}")
        if inline_data:
            parts.append({"text": f"Файл: {name}"})
            parts.append(
                {"inlineData": {"mimeType": media_type, "data": inline_data}}
            )
    parts.append(
        {
            "text": (
                f"Сообщение: {content or '[без текста]'}\n\n"
                + "\n\n".join(manifest)
            )
        }
    )
    result = await provider.generate_structured_parts(
        parts=parts,
        response_model=ChatIntentResultDTO,
        system_prompt=CHAT_ROUTER_PROMPT,
    )
    deterministic_groups = _deterministic_numbered_sheet_groups(content, documents)
    if not deterministic_groups:
        deterministic_groups = _deterministic_timetable_groups(documents)
    if deterministic_groups:
        result.data.intent = ChatIntent.BATCH_TRIP_REQUEST
        result.data.trip_groups = deterministic_groups
        result.data.confidence = 1.0
    return result


def native_inline_data(path: Path, media_type: str) -> str | None:
    if media_type == "application/pdf" or media_type.startswith("image/"):
        return base64.b64encode(path.read_bytes()).decode("ascii")
    return None


async def upsert_fleet(
    db: AsyncSession,
    *,
    tenant_id: str,
    result: ChatIntentResultDTO,
) -> dict[str, int]:
    counts = {
        "vehicles_created": 0,
        "vehicles_updated": 0,
        "vehicles_skipped": 0,
        "vehicles_expired": 0,
        "drivers_created": 0,
        "drivers_updated": 0,
    }
    for item in result.vehicles:
        plate = "".join(item.registration_plate.upper().split())
        vehicle = await db.scalar(
            select(Vehicle).where(
                Vehicle.tenant_id == tenant_id,
                Vehicle.registration_plate == plate,
            )
        )
        values = item.model_dump(exclude_none=True)
        values["registration_plate"] = plate
        if vehicle is None:
            if not item.make_model or item.seats is None:
                counts["vehicles_skipped"] += 1
                continue
            vehicle = Vehicle(tenant_id=tenant_id, is_active=True, **values)
            db.add(vehicle)
            counts["vehicles_created"] += 1
        else:
            for field, value in values.items():
                setattr(vehicle, field, value)
            vehicle.is_active = True
            counts["vehicles_updated"] += 1
        if (
            item.diagnostic_card_valid_until is None
            or item.diagnostic_card_valid_until <= date.today()
        ):
            counts["vehicles_expired"] += 1

    for item in result.drivers:
        license_number = "".join(item.license_number.split())
        driver = await db.scalar(
            select(Driver).where(
                Driver.tenant_id == tenant_id,
                Driver.license_number == license_number,
            )
        )
        values = item.model_dump(exclude_none=True)
        values["license_number"] = license_number
        if driver is None:
            driver = Driver(tenant_id=tenant_id, is_active=True, **values)
            db.add(driver)
            counts["drivers_created"] += 1
        else:
            for field, value in values.items():
                setattr(driver, field, value)
            driver.is_active = True
            counts["drivers_updated"] += 1
    await db.flush()
    return counts
