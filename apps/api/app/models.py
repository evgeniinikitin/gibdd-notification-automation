from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4_str() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="Администратор", nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="client_admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class CarrierProfile(Base, TimestampMixin):
    __tablename__ = "carrier_profiles"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    legal_name: Mapped[str | None] = mapped_column(String(500))
    short_name: Mapped[str | None] = mapped_column(String(255))
    inn: Mapped[str | None] = mapped_column(String(12))
    kpp: Mapped[str | None] = mapped_column(String(9))
    ogrn: Mapped[str | None] = mapped_column(String(15))
    legal_address: Mapped[str | None] = mapped_column(Text)
    actual_address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(320))
    director_position: Mapped[str | None] = mapped_column(String(255))
    director_full_name: Mapped[str | None] = mapped_column(String(255))
    license_number: Mapped[str | None] = mapped_column(String(128))
    license_issued_at: Mapped[date | None] = mapped_column(Date)


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("tenant_id", "registration_plate"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    make_model: Mapped[str] = mapped_column(String(255), nullable=False)
    registration_plate: Mapped[str] = mapped_column(String(16), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnostic_card_number: Mapped[str | None] = mapped_column(String(64))
    diagnostic_card_valid_until: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Driver(Base, TimestampMixin):
    __tablename__ = "drivers"
    __table_args__ = (UniqueConstraint("tenant_id", "license_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(32))
    license_number: Mapped[str] = mapped_column(String(64), nullable=False)
    license_categories: Mapped[str] = mapped_column(String(64), default="D", nullable=False)
    license_issued_at: Mapped[date | None] = mapped_column(Date)
    experience_from: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StoredFile(Base, TimestampMixin):
    __tablename__ = "stored_files"
    __table_args__ = (
        Index("ix_stored_files_tenant_kind", "tenant_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    uploaded_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    trip_id: Mapped[str | None] = mapped_column(
        ForeignKey("trips.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="attachment", nullable=False)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DocumentTemplate(Base, TimestampMixin):
    __tablename__ = "document_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("stored_files.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), default="notification", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TenantAsset(Base, TimestampMixin):
    __tablename__ = "tenant_assets"
    __table_args__ = (UniqueConstraint("tenant_id", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    file_id: Mapped[str] = mapped_column(
        ForeignKey("stored_files.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), default="Новая заявка", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Trip(Base, TimestampMixin):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("conversation_id"),
        Index("ix_trips_reference", "reference", unique=True),
        Index("ix_trips_tenant_status", "tenant_id", "status"),
        Index("ix_trips_tenant_date", "tenant_id", "trip_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    trip_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[str | None] = mapped_column(String(5))
    end_time: Mapped[str | None] = mapped_column(String(5))
    route_text: Mapped[str | None] = mapped_column(Text)
    customer_type: Mapped[str | None] = mapped_column(String(16))
    customer_name: Mapped[str | None] = mapped_column(String(500))
    customer_inn: Mapped[str | None] = mapped_column(String(12))
    customer_kpp: Mapped[str | None] = mapped_column(String(9))
    customer_address: Mapped[str | None] = mapped_column(Text)
    passenger_count: Mapped[int | None] = mapped_column(Integer)
    selected_vehicle_id: Mapped[str | None] = mapped_column(
        ForeignKey("vehicles.id", ondelete="SET NULL")
    )
    selected_driver_id: Mapped[str | None] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL")
    )
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    portal_token: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TripPayload(Base, TimestampMixin):
    """Immutable validated portal contract for one trip generation revision."""

    __tablename__ = "trip_payloads"
    __table_args__ = (
        UniqueConstraint("trip_id", "revision"),
        Index("ix_trip_payloads_tenant_trip", "tenant_id", "trip_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="validated", nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


class SubmissionJob(Base, TimestampMixin):
    """Persisted, idempotent generation and portal submission job."""

    __tablename__ = "submission_jobs"
    __table_args__ = (
        UniqueConstraint("trip_id"),
        Index("ix_submission_jobs_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False
    )
    payload_id: Mapped[str | None] = mapped_column(
        ForeignKey("trip_payloads.id", ondelete="SET NULL"), index=True
    )
    notification_docx_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL")
    )
    notification_pdf_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("stored_files.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    live_submission: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    final_submit_attempted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    portal_url: Mapped[str | None] = mapped_column(Text)
    portal_token: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubmissionAttempt(Base):
    """One browser attempt. Diagnostics are hidden from the client file archive."""

    __tablename__ = "submission_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number"),
        Index("ix_submission_attempts_tenant_job", "tenant_id", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("submission_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="started", nullable=False)
    final_submit_attempted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    current_url: Mapped[str | None] = mapped_column(Text)
    diagnostic_file_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TripReferenceCounter(Base):
    __tablename__ = "trip_reference_counters"

    period: Mapped[str] = mapped_column(String(6), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class BalanceAccount(Base, TimestampMixin):
    __tablename__ = "balance_accounts"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    available: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )
    reserved: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0"), nullable=False
    )


class BillingTransaction(Base):
    __tablename__ = "billing_transactions"
    __table_args__ = (Index("ix_billing_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("balance_accounts.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    trip_id: Mapped[str | None] = mapped_column(
        ForeignKey("trips.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
