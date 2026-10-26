from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class RegisterRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=255)
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)

    @field_validator("company_name", "full_name", mode="before")
    @classmethod
    def strip_names(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class TenantRead(ORMModel):
    id: str
    name: str


class UserRead(ORMModel):
    id: str
    tenant_id: str
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    tenant: TenantRead | None = None


class ProfileSettingsUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    company_name: str = Field(min_length=2, max_length=255)

    @field_validator("full_name", "company_name", mode="before")
    @classmethod
    def strip_names(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value


class CarrierProfileWrite(BaseModel):
    legal_name: str = Field(min_length=2, max_length=500)
    inn: str = Field(pattern=r"^\d{10}(\d{2})?$")
    legal_address: str = Field(min_length=5, max_length=2000)
    actual_address: str | None = Field(default=None, max_length=2000)
    phone: str = Field(min_length=5, max_length=32)
    email: EmailStr
    director_position: str = Field(min_length=2, max_length=255)
    director_full_name: str = Field(min_length=2, max_length=255)
    license_number: str | None = Field(default=None, max_length=128)
    license_issued_at: date | None = None


class CarrierProfilePatch(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=500)
    inn: str | None = Field(default=None, pattern=r"^\d{10}(\d{2})?$")
    legal_address: str | None = Field(default=None, min_length=5, max_length=2000)
    actual_address: str | None = Field(default=None, max_length=2000)
    phone: str | None = Field(default=None, min_length=5, max_length=32)
    email: EmailStr | None = None
    director_position: str | None = Field(default=None, min_length=2, max_length=255)
    director_full_name: str | None = Field(default=None, min_length=2, max_length=255)
    license_number: str | None = Field(default=None, max_length=128)
    license_issued_at: date | None = None


class CarrierProfileRead(ORMModel):
    id: str
    tenant_id: str
    legal_name: str | None
    inn: str | None
    legal_address: str | None
    actual_address: str | None
    phone: str | None
    email: EmailStr | None
    director_position: str | None
    director_full_name: str | None
    license_number: str | None
    license_issued_at: date | None
    created_at: datetime
    updated_at: datetime


class VehicleWrite(BaseModel):
    make_model: str = Field(min_length=2, max_length=255)
    registration_plate: str = Field(min_length=5, max_length=16)
    year: int | None = Field(default=None, ge=1950, le=2100)
    seats: int = Field(ge=1, le=120)
    diagnostic_card_number: str | None = Field(default=None, max_length=64)
    diagnostic_card_valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    is_active: bool = True

    @field_validator("registration_plate")
    @classmethod
    def normalize_plate(cls, value: str) -> str:
        return "".join(value.upper().split())


class VehicleRead(VehicleWrite, ORMModel):
    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime


class DriverWrite(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=32)
    license_number: str = Field(min_length=3, max_length=64)
    license_categories: str = Field(default="D", min_length=1, max_length=64)
    license_issued_at: date | None = None
    experience_from: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    is_active: bool = True


class DriverRead(DriverWrite, ORMModel):
    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime


class FileRead(ORMModel):
    id: str
    tenant_id: str
    conversation_id: str | None
    trip_id: str | None
    trip_reference: str | None = None
    kind: str
    original_name: str
    content_type: str
    size: int
    checksum_sha256: str
    created_at: datetime


class TemplateRead(ORMModel):
    id: str
    tenant_id: str
    file_id: str
    name: str
    kind: str
    version: int
    is_active: bool
    created_at: datetime


class AssetRead(ORMModel):
    id: str
    tenant_id: str
    file_id: str
    kind: Literal["signature", "stamp"]
    created_at: datetime


class OnboardingRequirement(BaseModel):
    key: str
    label: str
    completed: bool
    path: str


class OnboardingStatus(BaseModel):
    completed: bool
    completion_percent: int
    requirements: list[OnboardingRequirement]


class ConversationCreate(BaseModel):
    title: str = Field(default="Новая заявка", min_length=1, max_length=255)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["active", "archived"] | None = None


class ConversationRead(ORMModel):
    id: str
    tenant_id: str
    created_by_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(default="", max_length=50000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: str | None) -> str:
        return value.strip() if isinstance(value, str) else ""

    @model_validator(mode="after")
    def require_content_or_attachment(self) -> "MessageCreate":
        if not self.content and not self.attachment_ids:
            raise ValueError("Добавьте текст сообщения или вложение")
        return self


class MessageRead(ORMModel):
    id: str
    conversation_id: str
    author_id: str | None
    role: str
    content: str
    attachments: list[str]
    metadata_json: dict[str, Any]
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class TripCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    conversation_id: str | None = None
    trip_date: date | None = None
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    route_text: str | None = Field(default=None, max_length=20000)
    customer_type: Literal["legal", "ip", "physical"] | None = None
    customer_name: str | None = Field(default=None, max_length=500)
    customer_inn: str | None = Field(default=None, max_length=12)
    customer_kpp: str | None = Field(default=None, max_length=9)
    customer_address: str | None = Field(default=None, max_length=2000)
    passenger_count: int | None = Field(default=None, ge=0, le=10000)
    selected_vehicle_id: str | None = None
    selected_driver_id: str | None = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)


class TripUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    trip_date: date | None = None
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    route_text: str | None = Field(default=None, max_length=20000)
    customer_type: Literal["legal", "ip", "physical"] | None = None
    customer_name: str | None = Field(default=None, max_length=500)
    customer_inn: str | None = Field(default=None, max_length=12)
    customer_kpp: str | None = Field(default=None, max_length=9)
    customer_address: str | None = Field(default=None, max_length=2000)
    passenger_count: int | None = Field(default=None, ge=0, le=10000)
    selected_vehicle_id: str | None = None
    selected_driver_id: str | None = None
    extracted_data: dict[str, Any] | None = None


class TripProgressUpdate(BaseModel):
    status: Literal[
        "draft",
        "uploaded",
        "extracting",
        "clarifying",
        "needs_review",
        "validated",
        "ready",
        "confirmed",
        "generating",
        "generating_documents",
        "building_payload",
        "ready_to_submit",
        "awaiting_captcha",
        "submitting",
        "dry_run_ready",
        "submitted",
        "receipt_pending",
        "completed",
        "failed",
        "retry_scheduled",
        "needs_support",
        "cancelled",
    ]
    progress: int = Field(ge=0, le=100)


class TripRead(ORMModel):
    id: str
    reference: str
    tenant_id: str
    conversation_id: str | None
    created_by_id: str
    title: str
    status: str
    progress: int
    trip_date: date | None
    start_time: str | None
    end_time: str | None
    route_text: str | None
    customer_type: str | None
    customer_name: str | None
    customer_inn: str | None
    customer_kpp: str | None
    customer_address: str | None
    passenger_count: int | None
    selected_vehicle_id: str | None
    selected_driver_id: str | None
    extracted_data: dict[str, Any]
    failure_reason: str | None
    confirmed_at: datetime | None
    portal_token: str | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SubmissionAttemptRead(ORMModel):
    id: str
    attempt_number: int
    status: str
    final_submit_attempted: bool
    current_url: str | None
    diagnostic_file_ids: list[str]
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None


class TripAutomationRead(BaseModel):
    trip_id: str
    job_id: str | None = None
    status: str
    payload_id: str | None = None
    notification_docx_file_id: str | None = None
    notification_pdf_file_id: str | None = None
    live_submission: bool = False
    attempt_count: int = 0
    final_submit_attempted: bool = False
    portal_url: str | None = None
    portal_token: str | None = None
    last_error: str | None = None
    attempts: list[SubmissionAttemptRead] = Field(default_factory=list)
    diagnostic_files: list[FileRead] = Field(default_factory=list)


class BalanceRead(BaseModel):
    available: Decimal
    reserved: Decimal
    total: Decimal


class BalanceAdjustment(BaseModel):
    amount: Decimal = Field(gt=Decimal("-1000000"), lt=Decimal("1000000"))
    note: str = Field(min_length=3, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class TransactionRead(ORMModel):
    id: str
    kind: str
    amount: Decimal
    balance_after: Decimal
    note: str | None
    trip_id: str | None
    trip_reference: str | None = None
    actor_id: str | None
    actor_name: str | None = None
    actor_email: EmailStr | None = None
    created_at: datetime


class AuditEventRead(ORMModel):
    id: str
    actor_id: str | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict[str, Any]
    ip_address: str | None
    created_at: datetime


class AdminTenantRead(BaseModel):
    tenant_id: str
    company_name: str
    primary_admin_email: EmailStr | None
    primary_admin_full_name: str | None
    is_active: bool
    profile_completed: bool
    onboarding_completed: bool
    onboarding_completion_percent: int
    available_notifications: int
    reserved_notifications: int
    trip_count: int
    submitted_trip_count: int
    created_at: datetime
    updated_at: datetime


class AdminNotificationBalanceUpdate(BaseModel):
    available_notifications: int = Field(ge=0, le=9_999_999_999)
    note: str | None = Field(default=None, max_length=2000)


class AdminNotificationBalanceResult(BaseModel):
    tenant_id: str
    available_notifications: int
    reserved_notifications: int
    delta: int
    transaction_id: str
    updated_at: datetime


class Paginated(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    detail: str
