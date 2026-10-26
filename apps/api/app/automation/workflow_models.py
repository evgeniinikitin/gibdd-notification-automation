"""Database state for persisted chat-to-trip extraction runs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..models import TimestampMixin, uuid4_str


class TripWorkflowRun(Base, TimestampMixin):
    __tablename__ = "trip_workflow_runs"
    __table_args__ = (
        UniqueConstraint("trip_id", "revision"),
        Index(
            "ix_trip_workflow_runs_tenant_trip_created",
            "tenant_id",
            "trip_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trip_id: Mapped[str] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    triggered_by_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    requested_provider: Mapped[str | None] = mapped_column(String(64))
    requested_model: Mapped[str | None] = mapped_column(String(255))
    actual_provider: Mapped[str | None] = mapped_column(String(128))
    actual_model: Mapped[str | None] = mapped_column(String(255))
    response_id: Mapped[str | None] = mapped_column(String(255))
    attempt_count: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
