"""Typed trip extraction on top of a pluggable LLM provider.

The extraction contract intentionally has no carrier profile fields.  A source
document may mention a carrier, but that text is retained only as ignored
diagnostic metadata and can never be merged into the tenant carrier profile.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import EmailStr, Field, field_validator

from .openrouter import OpenRouterProvider
from .schemas import StrictDTO, StructuredLLMResultDTO
from .schemas import ImageOCRDTO


class ExtractionIntent(StrEnum):
    TRIP_REQUEST = "trip_request"
    CLARIFICATION = "clarification"
    UNKNOWN = "unknown"


class TripKind(StrEnum):
    TRANSFER = "transfer"
    EXCURSION = "excursion"
    SHUTTLE = "shuttle"
    OTHER = "other"
    UNKNOWN = "unknown"


class ExtractedCustomerDTO(StrictDTO):
    type: str | None = Field(default=None, pattern=r"^(legal|ip|physical)$")
    name: str | None = None
    inn: str | None = Field(default=None, pattern=r"^\d{10}(\d{2})?$")
    address: str | None = None
    phone: str | None = Field(default=None, min_length=5)
    email: EmailStr | None = None


class ExtractedChildDTO(StrictDTO):
    full_name: str
    birth_date: date | None = None
    parent_name: str | None = None
    parent_phone: str | None = None


class ExtractedCompanionDTO(StrictDTO):
    full_name: str
    role: str | None = None
    phone: str | None = None


class ExtractedRouteStopDTO(StrictDTO):
    address: str
    time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]|\d{3}):[0-5]\d$"
    )
    goal: str | None = None


class ExtractedRouteDTO(StrictDTO):
    text: str | None = None
    region_codes: list[str] = Field(default_factory=list)
    address_start: str | None = None
    destination: str | None = None
    district: str | None = Field(
        default=None,
        description=(
            "Всегда определи район начала маршрута по адресу "
            "подачи. Это системно вычисляемое поле, а не вопрос "
            "пользователю."
        ),
    )
    roads: str | None = None
    stops: list[ExtractedRouteStopDTO] = Field(default_factory=list)
    distance: str | None = Field(
        default=None,
        description=(
            "Всегда оцени общее расстояние маршрута в км по всем "
            "точкам. Допустима разумная приблизительная оценка."
        ),
    )
    duration: str | None = Field(
        default=None,
        pattern=r"^\d{1,3}:[0-5]\d$",
        description=(
            "Оцени суммарное время движения автобуса в ЧЧ:ММ без ожидания "
            "на экскурсии, мероприятии, стоянке или в лагере. Полный период "
            "работы задан отдельными start_time/end_time."
        ),
    )
    approximate: bool = False

    @field_validator("region_codes")
    @classmethod
    def normalize_region_codes(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            code = item.strip()
            if len(code) == 2 and code.isdigit() and code not in normalized:
                normalized.append(code)
        return normalized


class ExtractedTransportPreferencesDTO(StrictDTO):
    vehicle_registration_plate: str | None = None
    driver_name: str | None = None


class TripExtractionDTO(StrictDTO):
    """The only structured response accepted from a trip extraction model."""

    intent: ExtractionIntent = ExtractionIntent.TRIP_REQUEST
    trip_kind: TripKind = TripKind.UNKNOWN
    small_group_notification_confirmed: bool | None = None
    customer: ExtractedCustomerDTO | None = None
    trip_date: date | None = None
    start_time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    end_time: str | None = Field(
        default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$"
    )
    route: ExtractedRouteDTO | None = None
    children: list[ExtractedChildDTO] = Field(default_factory=list)
    companions: list[ExtractedCompanionDTO] = Field(default_factory=list)
    children_count: int | None = Field(default=None, ge=0, le=10_000)
    companions_count: int | None = Field(default=None, ge=0, le=10_000)
    transport_preferences: ExtractedTransportPreferencesDTO | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    uncertain_fields: list[str] = Field(default_factory=list)
    confirmed_fields: list[str] = Field(default_factory=list)
    ignored_carrier_mentions: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value: dict[str, float]) -> dict[str, float]:
        for field, confidence in value.items():
            if not field.strip():
                raise ValueError("confidence field name must not be empty")
            if confidence < 0 or confidence > 1:
                raise ValueError("confidence must be between 0 and 1")
        return value


def normalize_extracted_passenger_roles(
    extraction: TripExtractionDTO,
    *,
    source_text: str = "",
) -> TripExtractionDTO:
    """Normalize actual passengers without turning document signers into them.

    Source lists frequently put adult athletes and staff under a shared
    participant heading. Their legal role in the notification is determined
    by age on the actual trip date, not by the table section chosen by the
    document author or the LLM.
    """

    had_detailed_children = bool(extraction.children)
    children: list[ExtractedChildDTO] = []
    declared_companions_count = extraction.companions_count
    non_passenger_roles = re.compile(
        r"(?:^|\b)(?:\u0433\u0435\u043d\u0435\u0440\u0430\u043b\u044c\u043d(?:\u044b\u0439|\u043e\u0433\u043e)\s+)?\u0434\u0438\u0440\u0435\u043a\u0442\u043e\u0440(?:\u0430)?\b|"
        r"(?:^|\b)\u0433\u043b\u0430\u0432\u043d(?:\u044b\u0439|\u043e\u0433\u043e)\s+\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440(?:\u0430)?\b|"
        r"(?:^|\b)\u0431\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440(?:\u0430)?\b|(?:^|\b)\u043f\u043e\u0434\u043f\u0438\u0441\u0430\u043d\u0442(?:\u0430)?\b",
        re.IGNORECASE,
    )
    passenger_markers = re.compile(
        r"\u0441\u043e\u043f\u0440\u043e\u0432\u043e\u0436|\u043e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d|\u0442\u0440\u0435\u043d\u0435\u0440|\u043f\u0435\u0434\u0430\u0433\u043e\u0433|\u0443\u0447\u0430\u0441\u0442\u043d\u0438\u043a|\u0440\u043e\u0434\u0438\u0442\u0435\u043b",
        re.IGNORECASE,
    )
    companions = [
        item
        for item in extraction.companions
        if not (
            re.search(
                r"(?iu)\bмеханик\b|\bответственн\w*\s+за\s+перевозк\w*\b",
                item.role or "",
            )
            or (
                non_passenger_roles.search(item.role or "")
                and not passenger_markers.search(item.role or "")
            )
        )
        and len(re.findall(r"[A-Za-zА-Яа-яЁё-]+", item.full_name)) >= 2
    ]
    companion_names = {
        item.full_name.casefold().strip() for item in companions
    }
    for child in extraction.children:
        birth_date = child.birth_date
        is_adult = bool(
            extraction.trip_date
            and birth_date
            and (
                extraction.trip_date.year - birth_date.year
                - (
                    (extraction.trip_date.month, extraction.trip_date.day)
                    < (birth_date.month, birth_date.day)
                )
            )
            >= 18
        )
        if not is_adult:
            children.append(child)
            continue
        normalized_name = child.full_name.casefold().strip()
        if normalized_name not in companion_names:
            companions.append(
                ExtractedCompanionDTO(
                    full_name=child.full_name,
                    role="совершеннолетний участник",
                    phone=child.parent_phone,
                )
            )
            companion_names.add(normalized_name)

    extraction.children = children
    extraction.companions = companions
    if had_detailed_children:
        extraction.children_count = len(children)
    explicit_declared_count = bool(
        declared_companions_count is not None
        and re.search(
            rf"(?:{declared_companions_count}\s+(?:совершеннолет\w*\s+)?сопровожд\w*|"
            rf"сопровожд\w*[^\n]{{0,40}}\b{declared_companions_count}\b)",
            source_text,
            re.IGNORECASE,
        )
    )
    extraction.companions_count = (
        max(declared_companions_count or 0, len(companions))
        if explicit_declared_count
        else len(companions)
    )
    return extraction


def derive_missing_transfer_end_time(
    extraction: TripExtractionDTO,
    *,
    source_text: str,
    scheduling_hint: str = "",
) -> bool:
    """Derive transfer arrival when the source never states an arrival time."""

    if (
        extraction.trip_kind != TripKind.TRANSFER
        or extraction.route is None
        or not extraction.start_time
    ):
        return False
    labelled_end = re.search(
        r"(?iu)время\s+окончания\s*[:\-]?\s*"
        r"((?:[01]?\d|2[0-3])[.:][0-5]\d)",
        source_text,
    )
    if labelled_end:
        hours, minutes = re.split(r"[.:]", labelled_end.group(1))
        source_end = f"{int(hours):02d}:{minutes}"
        changed = extraction.end_time != source_end
        extraction.end_time = source_end
        extraction.confidence["trip.end_time"] = 0.99
        extraction.uncertain_fields = [
            field for field in extraction.uncertain_fields if field != "trip.end_time"
        ]
        return changed
    explicit_end = False
    if extraction.end_time:
        escaped = re.escape(extraction.end_time)
        explicit_end = bool(
            re.search(
                rf"(?:прибыт|окончан|на\s+месте\s+назначения|"
                rf"время\s+приезда|отпущен)[^\n]{{0,100}}\b{escaped}\b|"
                rf"\b{escaped}\b[^\n]{{0,100}}(?:прибыт|окончан|назначения)",
                source_text,
                re.IGNORECASE,
            )
        )
    if explicit_end:
        return False
    timeline = re.search(
        r"(?iu)маршрут:\s*(.+?)\.\s*Начало",
        scheduling_hint,
    )
    departure_times = (
        re.findall(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", timeline.group(1))
        if timeline
        else []
    )
    terminal_stop = extraction.route.stops[-1] if extraction.route.stops else None
    terminal_goal = (terminal_stop.goal or "").casefold() if terminal_stop else ""
    returns_to_start = bool(
        terminal_stop
        and extraction.route.address_start
        and terminal_stop.address.casefold().strip()
        == extraction.route.address_start.casefold().strip()
    )
    if (
        terminal_stop
        and terminal_stop.time
        and len(departure_times) <= 1
        and (
            returns_to_start
            or re.search(
                r"окончан|возвращ|прибыт|конечн|высад",
                terminal_goal,
                re.IGNORECASE,
            )
        )
    ):
        changed = extraction.end_time != terminal_stop.time
        extraction.end_time = terminal_stop.time
        if changed:
            extraction.confidence["trip.end_time"] = 0.95
            extraction.uncertain_fields = [
                field
                for field in extraction.uncertain_fields
                if field != "trip.end_time"
            ]
        return changed
    if not extraction.route.duration:
        return False
    duration = re.fullmatch(r"(\d{1,2}):(\d{2})", extraction.route.duration)
    if not duration:
        return False
    duration_minutes = int(duration.group(1)) * 60 + int(duration.group(2))
    base_time = departure_times[-1] if departure_times else extraction.start_time
    leg_count = max(1, len(departure_times))
    if len(departure_times) > 1:
        duration_minutes = max(1, round(duration_minutes / leg_count))
    start = datetime.strptime(base_time, "%H:%M")
    end = start + timedelta(minutes=duration_minutes)
    derived = end.strftime("%H:%M")
    changed = extraction.end_time != derived
    extraction.end_time = derived
    if changed:
        extraction.confidence["trip.end_time"] = 0.85
        extraction.uncertain_fields = [
            field for field in extraction.uncertain_fields if field != "trip.end_time"
        ]
    return changed


class ExtractionDocumentDTO(StrictDTO):
    file_id: str
    name: str
    media_type: str
    text: str
    warnings: list[str] = Field(default_factory=list)
    # Native document bytes are carried only inside the worker process. They
    # are excluded from prompt JSON, persisted metadata, Redis and logs.
    inline_data_base64: str | None = Field(default=None, exclude=True)


class TripExtractionContextDTO(StrictDTO):
    current_fields: dict
    carrier_profile_identity: dict[str, str | None] = Field(default_factory=dict)
    latest_user_message: str
    recent_user_messages: list[str] = Field(default_factory=list)
    documents: list[ExtractionDocumentDTO] = Field(default_factory=list)


class TripExtractionProvider(Protocol):
    async def extract_trip(
        self, context: TripExtractionContextDTO
    ) -> StructuredLLMResultDTO[TripExtractionDTO]: ...


SYSTEM_PROMPT = """\
Ты извлекаешь структурированные данные заявки на перевозку детей для Buscript.
Верни только JSON по переданной JSON Schema и не добавляй пояснений.

Критические правила:
1. Извлекай только реального заказчика и данные поездки. Заказчик — сторона,
   которая заказывает и оплачивает перевозку (плательщик, школа, турфирма, учреждение или
   лицо, от имени которого оформлена заявка). Перевозчик — автобусная компания,
   которая исполняет рейс. Никогда не принимай перевозчика, его подпись,
   печать, реквизиты, лицензию, автобус или водителя за заказчика. Идентичность
   перевозчика передана отдельно в carrier_profile_identity: любое совпадение
   с ней нужно исключить из customer и вернуть только в
   ignored_carrier_mentions. Ищи заказчика в шапке/полях «заказчик»,
   «клиент», «плательщик», «организатор», а главное — по обязанностям сторон:
   кто оплачивает, сообщает место/время подачи и отвечает за заказ, тот customer;
   кто предоставляет автобус, заменяет его при поломке и оказывает транспортное
   обслуживание — исполнитель/исходный перевозчик и не может быть customer.
   В тексте обращения и реквизитах
   другой стороны документа.
2. Не выдумывай значения, не используй прочерки, «не указано» или пустые
   заглушки. Отсутствующее значение оставляй null/пустым списком.
3. Определи trip_kind. Для трансфера (доставка из точки A в точку B) отсутствие
   времени прибытия не является причиной задавать вопрос: оцени
   route.distance и route.duration по адресам и самостоятельно рассчитай
   end_time = start_time + route.duration. Для экскурсии/заказа на период,
   где окончание нельзя вывести из маршрута, оставь end_time=null; 18:00 не
   подставляй.
4. Маршрут, построенный приблизительно, пометь route.approximate=true.
5. confirmed_fields содержит только dotted-path полей, которые пользователь
   явно сообщил или подтвердил в ПОСЛЕДНЕМ сообщении.
6. uncertain_fields содержит dotted-path каждого неоднозначного значения.
7. confidence использует те же dotted-path, например customer.name,
   trip.end_time, route.text, children.
8. Даты возвращай YYYY-MM-DD, время HH:MM. Регионы: Санкт-Петербург=78,
   Ленинградская область=47.
9. Списки детей и сопровождающих не смешивай. Не придумывай людей.
10. Если последнее сообщение является ответом на предыдущий вопрос, обнови
    соответствующее поле, сохранив остальные уже извлечённые данные.
11. Email заказчика заполняется системой из профиля перевозчика.
    Не помечай customer.email как недостающее или неуверенное поле.
12. Если маршрут или адреса указаны, обязательно самостоятельно заполни
    route.district, route.roads, route.distance и route.duration. В roads
    перечисли основные улицы, шоссе и трассы между точками. Можно давать разумную
    приблизительную оценку; эти поля не нужно помечать uncertain.
13. Приблизительный маршрут — это допустимый системный результат. Не требуй
    от пользователя подтверждать сам факт приблизительности.
14. Слова «трансфер», «встреча», «забрать», «доставить», вокзал/аэропорт и
    движение из одной точки в другую обычно означают trip_kind=transfer.
    Оценочные duration/end_time для трансфера не добавляй в uncertain_fields.
15. Если детей меньше 8, уведомление обычно не требуется. Не теряй данные,
    но не подтверждай его автоматически. Если пользователь после рекомендации
    явно просит всё равно продолжить, верни small_group_notification_confirmed=true.
16. При конфликте источников используй дату и условия из договора, заявки,
    приложения/спецификации или расчёта стоимости, а не из названия либо шапки
    списка пассажиров. Учитывай год в наиболее новом формальном документе.
17. Название учреждения в заголовке списка детей не означает, что оно заказчик.
    Заказчика определяй по фактической роли, а не формальному названию стороны.
    В некоторых договорах Buscript слова «Фрахтователь» и «Фрахтовщик» могут
    использоваться нестандартно. Если ООО обязуется предоставить автобус, а
    физическое лицо обязуется оплатить поездку и сообщать изменения заказа,
    customer — это физическое лицо. ООО в таком договоре является исходным
    исполнителем и должно попасть только в ignored_carrier_mentions.
18. Для customer-физлица ИНН не требуется. Если его отдельный адрес не указан,
    используй указанный в договоре адрес подачи как контактный адрес и не задавай
    пользователю вопрос только из-за отсутствия отдельного адреса регистрации.
19. Для приложения к агентскому договору customer ВСЕГДА сторона, явно
    обозначенная «Агент», а не «Принципал» и не учреждение из заголовка списка.
    Агент здесь оформляет транспорт у перевозчика для Принципала. Это правило
    имеет приоритет над предположением о конечном плательщике/получателе.
20. Роль пассажира определяется возрастом на дату поездки, а не заголовком
    таблицы. Лицо, которому на trip_date уже исполнилось 18 лет, не является
    ребёнком: помести его в companions, даже если оно записано среди спортсменов
    или участников. Не теряй его телефон.
21. Нормализуй географию по фактическому населённому пункту и всему контексту
    маршрута. Канцелярская запись вроде «СПб, Глебычево» не делает известный
    посёлок Глебычево частью Санкт-Петербурга. Для distance оцени дорожный, а не
    прямой путь; учитывай городской подъезд, КАД/трассы и округляй консервативно.
    duration оцени для автобуса с обычным городским трафиком, а не по идеальной
    скорости легкового автомобиля.
22. Формулировки «обратно — уточняется», «время возвращения уточняется» и
    аналогичные означают, что поездка предполагает обратное плечо, но время
    окончания пока неизвестно. Это НЕ односторонний transfer: оставь end_time
    null, чтобы система спросила пользователя. В route.text/расстоянии/дорогах
    учитывай возврат в исходную точку и добавь её финальной остановкой, но не
    выдумывай время возвращения.
23. Если в документе несколько телефонов заказчика, предпочитай явно
    подписанный телефон ответственного лица, исполнителя заявки или контакт для
    поездки. Общий телефон/факс из шапки используй только когда персонального
    контактного номера нет.
24. route.duration — суммарное время ДВИЖЕНИЯ по всем плечам маршрута без
    ожидания. trip.start_time/end_time — полный период работы автобуса. Для
    A–B–A оцени движение туда и обратно; только для чистого трансфера A–B без
    ожидания route.duration обычно равно разнице end_time-start_time.
25. ФИО возле подписи/печати (директор, главный бухгалтер и т. п.) — это
    подписанты документа, а не сопровождающие. Не добавляй их в companions, если они
    отдельно не указаны как ответственные/тренеры/сопровождающие/участники поездки.
26. latest_user_message может быть системной подсказкой для одной заявки из пакета.
    Если в ней указан конкретный автобус/поездка/время, извлекай только этот экземпляр,
    а не смесь всех вариантов времени из исходного файла.
"""


class OpenRouterTripExtractionProvider:
    """Adapter that keeps the generic OpenRouter client out of orchestration."""

    def __init__(self, provider: OpenRouterProvider) -> None:
        self.provider = provider

    async def extract_trip(
        self, context: TripExtractionContextDTO
    ) -> StructuredLLMResultDTO[TripExtractionDTO]:
        prompt = (
            "Контекст заявки:\n"
            + json.dumps(
                context.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return await self.provider.generate_structured(
            prompt=prompt,
            response_model=TripExtractionDTO,
            system_prompt=SYSTEM_PROMPT,
        )


class MockTripExtractionProvider:
    """Deterministic explicit dev/test provider with no manufactured values.

    It is deliberately small and accepts labeled plain text.  It lets a fresh
    local installation exercise the complete UI/persistence pipeline before a
    real key is added, while normal startup still defaults to OpenRouter.
    """

    _date_pattern = re.compile(
        r"(?P<day>\d{1,2})[./-](?P<month>\d{1,2})[./-](?P<year>\d{4})"
    )

    async def extract_trip(
        self, context: TripExtractionContextDTO
    ) -> StructuredLLMResultDTO[TripExtractionDTO]:
        document_text = "\n".join(item.text for item in context.documents)
        text = "\n".join(
            item
            for item in (document_text, context.latest_user_message)
            if item.strip()
        )
        latest = context.latest_user_message
        customer_type = self._party_type(self._field(text, "Тип заказчика"))
        customer_name = self._field(text, "Заказчик")
        customer = (
            ExtractedCustomerDTO(
                type=customer_type,
                name=customer_name,
                inn=self._field(text, "ИНН"),
                address=self._field(text, "Адрес заказчика")
                or self._field(text, "Адрес"),
                phone=self._field(text, "Телефон заказчика")
                or self._field(text, "Телефон"),
                email=self._field(text, "Email заказчика")
                or self._field(text, "Email"),
            )
            if customer_type
            or customer_name
            or self._field(text, "ИНН")
            else None
        )
        trip_date = self._date(
            self._field(text, "Дата поездки") or self._field(text, "Дата")
        )
        start_time = self._time(
            self._field(text, "Время начала")
            or self._field(text, "Начало")
        )
        end_time = self._time(
            self._field(text, "Время окончания")
            or self._field(text, "Окончание")
        )
        route_text = self._field(text, "Маршрут")
        regions = re.findall(r"\b(?:47|78)\b", self._field(text, "Регионы") or "")
        address_start = self._field(text, "Адрес отправления")
        destination = self._field(text, "Пункт назначения")
        district = self._field(text, "Район")
        roads = self._field(text, "Дороги")
        distance = self._field(text, "Расстояние")
        duration = self._duration(self._field(text, "Продолжительность"))
        children = self._people(text, "Ребенок", child=True)
        companions = self._people(text, "Сопровождающий", child=False)
        vehicle = self._field(text, "Автобус")
        driver = self._field(text, "Водитель")
        latest_paths = {
            path
            for label, path in {
                "Тип заказчика": "customer.type",
                "Заказчик": "customer.name",
                "ИНН": "customer.inn",
                "Адрес": "customer.address",
                "Телефон": "customer.phone",
                "Email": "customer.email",
                "Дата": "trip.trip_date",
                "Начало": "trip.start_time",
                "Окончание": "trip.end_time",
                "Маршрут": "route.text",
                "Регионы": "route.region_codes",
                "Адрес отправления": "route.address_start",
                "Пункт назначения": "route.destination",
                "Район": "route.district",
                "Дороги": "route.roads",
                "Расстояние": "route.distance",
                "Продолжительность": "route.duration",
                "Ребенок": "children",
                "Сопровождающий": "companions",
                "Автобус": "transport_preferences.vehicle_registration_plate",
                "Водитель": "transport_preferences.driver_name",
            }.items()
            if re.search(rf"(?im)^\s*{re.escape(label)}[^:]*\s*:", latest)
        }
        extraction = TripExtractionDTO(
            intent=(
                ExtractionIntent.CLARIFICATION
                if context.current_fields
                else ExtractionIntent.TRIP_REQUEST
            ),
            customer=customer,
            trip_date=trip_date,
            start_time=start_time,
            end_time=end_time,
            route=(
                ExtractedRouteDTO(
                    text=route_text,
                    region_codes=regions,
                    address_start=address_start,
                    destination=destination,
                    district=district,
                    roads=roads,
                    distance=distance,
                    duration=duration,
                    approximate=False,
                )
                if any(
                    (
                        route_text,
                        regions,
                        address_start,
                        destination,
                        district,
                        roads,
                        distance,
                        duration,
                    )
                )
                else None
            ),
            children=children,
            companions=companions,
            children_count=len(children) or None,
            companions_count=len(companions) or None,
            transport_preferences=(
                ExtractedTransportPreferencesDTO(
                    vehicle_registration_plate=vehicle,
                    driver_name=driver,
                )
                if vehicle or driver
                else None
            ),
            confirmed_fields=sorted(latest_paths),
            confidence={path: 1.0 for path in latest_paths},
            ignored_carrier_mentions=[
                mention
                for mention in [self._field(text, "Перевозчик")]
                if mention
            ],
        )
        return StructuredLLMResultDTO[TripExtractionDTO](
            data=extraction,
            provider="mock",
            requested_model="buscript/mock-rule-based",
            actual_model="buscript/mock-rule-based",
            response_id=None,
            attempt_count=1,
        )

    async def extract_image_text(
        self, image_bytes: bytes, mime_type: str
    ) -> StructuredLLMResultDTO[ImageOCRDTO]:
        # A deterministic mock must not pretend it recognized an image.
        return StructuredLLMResultDTO[ImageOCRDTO](
            data=ImageOCRDTO(text="", confidence=0),
            provider="mock",
            requested_model="buscript/mock-no-vision",
            actual_model="buscript/mock-no-vision",
            response_id=None,
            attempt_count=1,
        )

    @staticmethod
    def _field(text: str, label: str) -> str | None:
        match = re.search(
            rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
            text,
        )
        return match.group(1).strip() if match else None

    @staticmethod
    def _party_type(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.casefold()
        if "физ" in normalized:
            return "physical"
        if normalized.strip() == "ип" or "индивидуаль" in normalized:
            return "ip"
        if "юр" in normalized or "организац" in normalized:
            return "legal"
        return value if value in {"legal", "ip", "physical"} else None

    @classmethod
    def _date(cls, value: str | None) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            match = cls._date_pattern.search(value)
            if not match:
                return None
            return date(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )

    @staticmethod
    def _time(value: str | None) -> str | None:
        if not value:
            return None
        match = re.search(r"\b([01]?\d|2[0-3])[:.](\d{2})\b", value)
        if not match:
            return None
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    @staticmethod
    def _duration(value: str | None) -> str | None:
        if not value:
            return None
        match = re.search(r"\b(\d{1,3})[:.](\d{2})\b", value)
        if not match or int(match.group(2)) > 59:
            return None
        return f"{int(match.group(1)):02d}:{match.group(2)}"

    @classmethod
    def _people(
        cls,
        text: str,
        label: str,
        *,
        child: bool,
    ) -> list[ExtractedChildDTO] | list[ExtractedCompanionDTO]:
        rows = re.findall(
            rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
            text,
        )
        parsed: list = []
        for row in rows:
            fields = [item.strip() for item in row.split(";")]
            if not fields or not fields[0]:
                continue
            if child:
                parsed.append(
                    ExtractedChildDTO(
                        full_name=fields[0],
                        birth_date=cls._date(fields[1]) if len(fields) > 1 else None,
                        parent_name=fields[2] if len(fields) > 2 else None,
                        parent_phone=fields[3] if len(fields) > 3 else None,
                    )
                )
            else:
                parsed.append(
                    ExtractedCompanionDTO(
                        full_name=fields[0],
                        role=fields[1] if len(fields) > 1 else None,
                        phone=fields[2] if len(fields) > 2 else None,
                    )
                )
        return parsed
