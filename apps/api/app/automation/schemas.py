"""Typed contracts shared by Buscript automation services.

The browser worker consumes :class:`GibddPayloadDTO` only.  Draft/profile DTOs
are deliberately separate so that data extracted from a customer document
cannot accidentally override the carrier stored in the tenant profile.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


PORTAL_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
DISPLAY_DATE_FORMAT = "%d.%m.%Y"
PORTAL_DATETIME_PATTERN = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"
DISPLAY_DATE_PATTERN = r"^\d{2}\.\d{2}\.\d{4}$"
HH_MM_PATTERN = r"^\d{2,3}:\d{2}$"


class StrictDTO(BaseModel):
    """Base DTO with closed objects and normalized string input."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class PartyType(StrEnum):
    LEGAL = "legal"
    IP = "ip"
    PHYSICAL = "physical"


class RuleSeverity(StrEnum):
    BLOCKING = "blocking"
    QUESTION = "question"
    WARNING = "warning"


class CaptchaMode(StrEnum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    LLM_OCR = "llm_ocr"
    PROVIDER = "provider"


class SubmissionStatus(StrEnum):
    DRY_RUN_READY = "dry_run_ready"
    SUBMITTED = "submitted"
    NEEDS_SUPPORT = "needs_support"
    FAILED = "failed"


class ApplicantDTO(StrictDTO):
    name: str = Field(min_length=1)
    position: str = Field(min_length=1)
    phone: str = Field(min_length=5)
    email: str = Field(min_length=3)


class PartyDTO(StrictDTO):
    """A customer or carrier in the exact shape expected by the portal."""

    type: PartyType
    entity_name: str | None = Field(default=None, min_length=1)
    entity_address: str | None = Field(default=None, min_length=1)
    entity_location: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    address: str | None = Field(default=None, min_length=1)
    phone: str = Field(min_length=5)
    email: str = Field(min_length=3)
    inn: str | None = Field(default=None, min_length=5)

    @model_validator(mode="after")
    def validate_party_shape(self) -> PartyDTO:
        if self.type in {PartyType.LEGAL, PartyType.IP}:
            missing = [
                field
                for field in (
                    "entity_name",
                    "entity_address",
                    "entity_location",
                    "inn",
                )
                if not getattr(self, field)
            ]
            if missing:
                raise ValueError(
                    f"{self.type.value} party requires: {', '.join(missing)}"
                )
        elif self.type is PartyType.PHYSICAL:
            missing = [
                field for field in ("name", "address") if not getattr(self, field)
            ]
            if missing:
                raise ValueError(
                    f"physical party requires: {', '.join(missing)}"
                )
            if self.inn is not None:
                raise ValueError("physical party must not contain inn")
        return self


class CarrierProfileDTO(StrictDTO):
    """The tenant's single carrier profile.

    Fields remain optional here because an empty profile is a valid onboarding
    state.  Business rules turn missing fields into actionable issues before
    document generation or submission.
    """

    tenant_id: str = Field(min_length=1)
    carrier_key: str | None = Field(default=None, min_length=1)
    party: PartyDTO | None = None
    applicant: ApplicantDTO | None = None
    template_artifact_id: str | None = Field(default=None, min_length=1)
    signature_artifact_id: str | None = Field(default=None, min_length=1)
    seal_artifact_id: str | None = Field(default=None, min_length=1)
    active: bool = True

    def missing_onboarding_fields(self) -> tuple[str, ...]:
        fields: list[str] = []
        for name in (
            "carrier_key",
            "party",
            "applicant",
            "signature_artifact_id",
        ):
            if getattr(self, name) is None:
                fields.append(name)
        if not self.active:
            fields.append("active")
        return tuple(fields)


class VehicleProfileDTO(StrictDTO):
    id: str = Field(min_length=1)
    brand_and_model: str = Field(min_length=1)
    number: str = Field(min_length=1)
    diagnostic_card_number: str = Field(min_length=1)
    diagnostic_card_valid_until: date
    navigator_info: bool = True
    active: bool = True
    blocked: bool = False

    @property
    def diagnostic_card_info(self) -> str:
        return (
            f"{self.diagnostic_card_number} до "
            f"{self.diagnostic_card_valid_until.strftime(DISPLAY_DATE_FORMAT)} г."
        )


class DriverProfileDTO(StrictDTO):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    licence_issue_date: date
    birth_date: date | None = None
    experience: str = Field(default="более 3х лет", min_length=1)
    active: bool = True
    blocked: bool = False


class TripDraftDTO(StrictDTO):
    tenant_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    children_count: int = Field(ge=1)
    date_start: datetime
    date_end: datetime | None = None
    selected_vehicle_ids: list[str] = Field(default_factory=list)
    selected_driver_ids: list[str] = Field(default_factory=list)
    # Kept for audit/UI only. It is never used by the payload builder.
    source_carrier_text: str | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> TripDraftDTO:
        if self.date_end is not None and self.date_end <= self.date_start:
            raise ValueError("date_end must be later than date_start")
        return self


class ExistingAllocationDTO(StrictDTO):
    trip_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    date_start: datetime
    date_end: datetime
    vehicle_ids: list[str] = Field(default_factory=list)
    driver_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_interval(self) -> ExistingAllocationDTO:
        if self.date_end <= self.date_start:
            raise ValueError("date_end must be later than date_start")
        return self


class RuleIssueDTO(StrictDTO):
    code: str = Field(min_length=1)
    severity: RuleSeverity
    field: str = Field(min_length=1)
    message: str = Field(min_length=1)
    suggested_value: str | None = None
    conflicting_trip_id: str | None = None


class BusinessRulesResultDTO(StrictDTO):
    valid: bool
    required_vehicle_count: int = Field(ge=1)
    required_driver_count: int = Field(ge=1)
    carrier_key: str | None = None
    issues: list[RuleIssueDTO] = Field(default_factory=list)

    @property
    def requires_user_input(self) -> bool:
        return any(issue.severity is RuleSeverity.QUESTION for issue in self.issues)


class TransportationInputDTO(StrictDTO):
    region_codes: list[str] = Field(min_length=1)
    children_count: int = Field(ge=1)
    goal: str = Field(default="Перевозка детей", min_length=1)
    date_start: datetime
    date_end: datetime

    @field_validator("region_codes")
    @classmethod
    def validate_regions(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"\d{2}", item) for item in value):
            raise ValueError("region codes must contain exactly two digits")
        if len(value) != len(set(value)):
            raise ValueError("region codes must be unique")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> TransportationInputDTO:
        if self.date_end <= self.date_start:
            raise ValueError("date_end must be later than date_start")
        return self


class PortalTransportationDTO(StrictDTO):
    region_codes: list[str] = Field(min_length=1)
    children_count: int = Field(ge=1)
    goal: str = Field(default="Перевозка детей", min_length=1)
    date_start: str = Field(pattern=PORTAL_DATETIME_PATTERN)
    date_end: str = Field(pattern=PORTAL_DATETIME_PATTERN)

    @field_validator("region_codes")
    @classmethod
    def validate_regions(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"\d{2}", item) for item in value):
            raise ValueError("region codes must contain exactly two digits")
        if len(value) != len(set(value)):
            raise ValueError("region codes must be unique")
        return value

    @model_validator(mode="after")
    def validate_interval(self) -> PortalTransportationDTO:
        start = datetime.strptime(self.date_start, PORTAL_DATETIME_FORMAT)
        end = datetime.strptime(self.date_end, PORTAL_DATETIME_FORMAT)
        if end <= start:
            raise ValueError("date_end must be later than date_start")
        return self


class PortalVehicleDTO(StrictDTO):
    brand_and_model: str = Field(min_length=1)
    number: str = Field(min_length=1)
    diagnostic_card_info: str = Field(min_length=1)
    navigator_info: bool = True


class PortalDriverDTO(StrictDTO):
    name: str = Field(min_length=1)
    licence: str = Field(min_length=1)
    licence_date: str = Field(pattern=DISPLAY_DATE_PATTERN)
    experience: str = Field(default="более 3х лет", min_length=1)

    @field_validator("licence_date")
    @classmethod
    def validate_real_date(cls, value: str) -> str:
        datetime.strptime(value, DISPLAY_DATE_FORMAT)
        return value


class RouteStopDTO(StrictDTO):
    address: str = Field(min_length=1)
    map_query: str | None = None
    time: str = Field(pattern=HH_MM_PATTERN)
    goal: str = Field(min_length=1)

    @field_validator("time")
    @classmethod
    def validate_minutes(cls, value: str) -> str:
        _, minutes = value.split(":")
        if int(minutes) > 59:
            raise ValueError("minutes must be between 00 and 59")
        return value


class RouteDTO(StrictDTO):
    address_start: str = Field(min_length=1)
    address_start_map_query: str | None = None
    route_region_code: str = Field(pattern=r"^\d{2}$")
    route_region_name: str | None = None
    district: str = Field(min_length=1)
    roads: str = Field(min_length=1)
    schedule: str | None = Field(default=None, min_length=1)
    stop_points: list[RouteStopDTO] = Field(min_length=1)
    distance: str = Field(min_length=1)
    duration: str = Field(pattern=HH_MM_PATTERN)
    use_map: bool = False

    @field_validator("duration")
    @classmethod
    def validate_minutes(cls, value: str) -> str:
        _, minutes = value.split(":")
        if int(minutes) > 59:
            raise ValueError("minutes must be between 00 and 59")
        return value

    @model_validator(mode="after")
    def normalize_schedule(self) -> RouteDTO:
        if self.schedule is None:
            self.schedule = self.roads
        if self.schedule != self.roads:
            raise ValueError("route.roads and route.schedule must be identical")
        return self


def validate_storage_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("storage key must not be empty")
    if "\x00" in normalized:
        raise ValueError("storage key contains a null byte")
    if normalized.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", normalized):
        raise ValueError("payload files must be storage keys, not absolute paths")
    parts = normalized.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("storage key must not traverse parent directories")
    return normalized


class GibddPayloadDTO(StrictDTO):
    tenant_id: str = Field(min_length=1)
    carrier_key: str = Field(min_length=1)
    applicant: ApplicantDTO
    transportation: PortalTransportationDTO
    customer: PartyDTO
    carrier: PartyDTO
    vehicles: list[PortalVehicleDTO] = Field(min_length=1)
    drivers: list[PortalDriverDTO] = Field(min_length=1)
    route: RouteDTO
    files: list[str] = Field(min_length=1)

    @field_validator("files")
    @classmethod
    def validate_file_keys(cls, value: list[str]) -> list[str]:
        keys = [validate_storage_key(item) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("file storage keys must be unique")
        return keys

    @model_validator(mode="after")
    def validate_cross_field_rules(self) -> GibddPayloadDTO:
        if len(self.vehicles) != len(self.drivers):
            raise ValueError("vehicles and drivers counts must match")
        if self.transportation.children_count > 55 and len(self.vehicles) < 2:
            raise ValueError("more than 55 children require at least two vehicles")
        if len({item.number for item in self.vehicles}) != len(self.vehicles):
            raise ValueError("vehicle numbers must be unique")
        if len({item.licence for item in self.drivers}) != len(self.drivers):
            raise ValueError("driver licences must be unique")
        return self


class PayloadBuildInputDTO(StrictDTO):
    tenant_id: str = Field(min_length=1)
    carrier_profile: CarrierProfileDTO
    transportation: TransportationInputDTO
    customer: PartyDTO
    vehicles: list[VehicleProfileDTO] = Field(min_length=1)
    drivers: list[DriverProfileDTO] = Field(min_length=1)
    route: RouteDTO
    files: list[str] = Field(min_length=1)

    @field_validator("files")
    @classmethod
    def validate_file_keys(cls, value: list[str]) -> list[str]:
        return [validate_storage_key(item) for item in value]


class CaptchaRecognitionDTO(StrictDTO):
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ImageOCRDTO(StrictDTO):
    text: str
    confidence: float = Field(ge=0, le=1)


class LLMUsageDTO(StrictDTO):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


T = TypeVar("T")


class StructuredLLMResultDTO(StrictDTO, Generic[T]):
    data: T
    provider: str = Field(default="openrouter", min_length=1)
    requested_model: str = Field(min_length=1)
    actual_model: str = Field(min_length=1)
    response_id: str | None = None
    usage: LLMUsageDTO | None = None
    attempt_count: int = Field(ge=1)


class CaptchaInputDTO(StrictDTO):
    image_bytes: bytes
    mime_type: str = Field(pattern=r"^image/[A-Za-z0-9.+-]+$")
    job_id: str = Field(min_length=1)
    page_url: str = Field(min_length=1)
    artifact_id: str | None = None


class CaptchaAttemptDTO(StrictDTO):
    attempt_number: int = Field(ge=1)
    mode: CaptchaMode
    result: str = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider: str | None = None
    model: str | None = None


class CaptchaResultDTO(StrictDTO):
    text: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    mode: CaptchaMode
    provider: str | None = None
    model: str | None = None
    artifact_id: str | None = None
    attempts: list[CaptchaAttemptDTO] = Field(default_factory=list)


class ChildDocumentRowDTO(StrictDTO):
    full_name: str = Field(min_length=1)
    birth_date: date
    parent_name: str = Field(min_length=1)
    parent_phone: str = Field(min_length=5)


class CompanionDocumentRowDTO(StrictDTO):
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    phone: str = Field(min_length=5)


class BusDocumentRowDTO(StrictDTO):
    model: str = Field(min_length=1)
    number: str = Field(min_length=1)
    diagnostic_card_info: str = Field(min_length=1)


class DriverDocumentRowDTO(StrictDTO):
    name: str = Field(min_length=1)
    birth_date: date | None = None
    licence: str = Field(min_length=1)
    licence_issue_date: date
    experience: str = Field(default="более 3х лет", min_length=1)


class DocumentGenerationRequestDTO(StrictDTO):
    tenant_id: str = Field(min_length=1)
    template_path: Path
    target_path: Path
    trip_date: date
    replacements: dict[str, str]
    children: list[ChildDocumentRowDTO] = Field(min_length=1)
    companions: list[CompanionDocumentRowDTO] = Field(min_length=1)
    buses: list[BusDocumentRowDTO] = Field(min_length=1)
    drivers: list[DriverDocumentRowDTO] = Field(min_length=1)
    route_lines: list[str] = Field(min_length=1)
    signature_path: Path | None = None
    stamp_path: Path | None = None

    @field_validator("replacements")
    @classmethod
    def validate_replacements(cls, value: dict[str, str]) -> dict[str, str]:
        for placeholder, replacement in value.items():
            if not re.fullmatch(r"\{\{[A-Z0-9_]+\}\}", placeholder):
                raise ValueError(f"invalid placeholder key: {placeholder}")
            if not replacement.strip():
                raise ValueError(f"empty replacement is forbidden: {placeholder}")
        return value

    @model_validator(mode="after")
    def validate_resource_counts(self) -> DocumentGenerationRequestDTO:
        if len(self.buses) != len(self.drivers):
            raise ValueError("document buses and drivers counts must match")
        if len(self.children) > 55 and len(self.buses) < 2:
            raise ValueError("more than 55 children require at least two buses")
        return self


class DocumentGenerationResultDTO(StrictDTO):
    path: Path
    child_count: int = Field(ge=1)
    companion_count: int = Field(ge=1)
    bus_count: int = Field(ge=1)
    driver_count: int = Field(ge=1)
    remaining_placeholders: list[str] = Field(default_factory=list)


class ParsedTableDTO(StrictDTO):
    source: str = Field(min_length=1)
    rows: list[list[str]]


class ParsedDocumentDTO(StrictDTO):
    path: Path
    media_type: str = Field(min_length=1)
    text: str
    tables: list[ParsedTableDTO] = Field(default_factory=list)
    page_count: int | None = Field(default=None, ge=0)
    sheet_count: int | None = Field(default=None, ge=0)
    image_width: int | None = Field(default=None, ge=1)
    image_height: int | None = Field(default=None, ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provider: str | None = None
    model: str | None = None
    warnings: list[str] = Field(default_factory=list)


class SubmissionOutcomeDTO(StrictDTO):
    status: SubmissionStatus
    final_submit_attempted: bool
    current_url: str | None = None
    token: str | None = None
    captcha_mode: CaptchaMode | None = None
    message: str | None = None
    screenshots: list[Path] = Field(default_factory=list)


class ReceiptParseResultDTO(StrictDTO):
    system_id: str | None = None
    text: str = ""
    page_count: int = Field(default=0, ge=0)
    needs_review: bool = False


def portal_datetime(value: datetime) -> str:
    return value.strftime(PORTAL_DATETIME_FORMAT)


def display_date(value: date) -> str:
    return value.strftime(DISPLAY_DATE_FORMAT)


def json_compatible(value: Any) -> Any:
    """Small helper used by adapters that require plain JSON-compatible data."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
