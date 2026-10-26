from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TripReferenceCounter


async def next_trip_reference(
    db: AsyncSession,
    *,
    created_at: datetime | None = None,
) -> str:
    """Atomically allocate a human-readable, database-backed trip reference."""

    moment = created_at or datetime.now(timezone.utc)
    period = moment.strftime("%Y%m")
    dialect_name = db.bind.dialect.name if db.bind is not None else ""

    if dialect_name == "sqlite":
        statement = (
            sqlite_insert(TripReferenceCounter)
            .values(period=period, last_value=1)
            .on_conflict_do_update(
                index_elements=[TripReferenceCounter.period],
                set_={"last_value": TripReferenceCounter.last_value + 1},
            )
            .returning(TripReferenceCounter.last_value)
        )
        sequence = int((await db.execute(statement)).scalar_one())
    elif dialect_name == "postgresql":
        statement = (
            postgresql_insert(TripReferenceCounter)
            .values(period=period, last_value=1)
            .on_conflict_do_update(
                index_elements=[TripReferenceCounter.period],
                set_={"last_value": TripReferenceCounter.last_value + 1},
            )
            .returning(TripReferenceCounter.last_value)
        )
        sequence = int((await db.execute(statement)).scalar_one())
    else:
        counter = await db.scalar(
            select(TripReferenceCounter)
            .where(TripReferenceCounter.period == period)
            .with_for_update()
        )
        if counter is None:
            counter = TripReferenceCounter(period=period, last_value=1)
            db.add(counter)
            await db.flush()
        else:
            counter.last_value += 1
        sequence = counter.last_value

    return f"BS-{period}-{sequence:04d}"
