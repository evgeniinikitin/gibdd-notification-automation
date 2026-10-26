from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings, get_settings
from .database import Base, make_engine, make_session_factory
from .models import BalanceAccount, BillingTransaction, Tenant, User
from .routers import admin, audit, auth, billing, conversations, files, fleet, onboarding, trips
from .security import hash_password

logger = logging.getLogger("buscript")


async def _sqlite_compatibility_migrations(connection) -> None:
    """Bring databases created by pre-Alembic local builds forward in-place."""

    if connection.dialect.name != "sqlite":
        return

    tenant_columns = {
        row[1]
        for row in (await connection.exec_driver_sql('PRAGMA table_info("tenants")')).all()
    }
    if "is_system" not in tenant_columns:
        await connection.exec_driver_sql(
            "ALTER TABLE tenants ADD COLUMN is_system BOOLEAN NOT NULL DEFAULT 0"
        )

    trip_columns = {
        row[1]
        for row in (await connection.exec_driver_sql('PRAGMA table_info("trips")')).all()
    }
    if "reference" not in trip_columns:
        await connection.exec_driver_sql(
            "ALTER TABLE trips ADD COLUMN reference VARCHAR(32)"
        )
    if "failure_reason" not in trip_columns:
        await connection.exec_driver_sql(
            "ALTER TABLE trips ADD COLUMN failure_reason TEXT"
        )
    rows = (
        await connection.execute(
            text(
                "SELECT id, reference, created_at FROM trips "
                "ORDER BY created_at ASC, id ASC"
            )
        )
    ).all()
    period_maximums: dict[str, int] = {}
    missing: list[tuple[str, str]] = []
    pattern = re.compile(r"^BS-(\d{6})-(\d+)$")
    for trip_id, reference, created_at in rows:
        match = pattern.fullmatch(reference or "")
        if match:
            period = match.group(1)
            period_maximums[period] = max(
                period_maximums.get(period, 0),
                int(match.group(2)),
            )
            continue
        if reference:
            # Never rewrite a non-empty identifier that may have been assigned
            # by an intermediate build or imported from another system.
            continue
        try:
            moment = (
                created_at
                if isinstance(created_at, datetime)
                else datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            )
        except (TypeError, ValueError):
            moment = datetime.now(timezone.utc)
        period = moment.strftime("%Y%m")
        next_value = period_maximums.get(period, 0) + 1
        period_maximums[period] = next_value
        missing.append((trip_id, f"BS-{period}-{next_value:04d}"))

    for trip_id, reference in missing:
        await connection.execute(
            text("UPDATE trips SET reference = :reference WHERE id = :trip_id"),
            {"reference": reference, "trip_id": trip_id},
        )
    for period, last_value in period_maximums.items():
        await connection.execute(
            text(
                "INSERT INTO trip_reference_counters(period, last_value, updated_at) "
                "VALUES (:period, :last_value, CURRENT_TIMESTAMP) "
                "ON CONFLICT(period) DO UPDATE SET "
                "last_value = MAX(last_value, excluded.last_value), "
                "updated_at = CURRENT_TIMESTAMP"
            ),
            {"period": period, "last_value": last_value},
        )

    await connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_trips_reference ON trips(reference)"
    )
    linked_trips = (
        await connection.execute(
            text(
                "SELECT id, conversation_id, status, updated_at, created_at "
                "FROM trips WHERE conversation_id IS NOT NULL "
                "ORDER BY conversation_id ASC, "
                "CASE status "
                "WHEN 'submitted' THEN 0 "
                "WHEN 'submitting' THEN 1 "
                "WHEN 'confirmed' THEN 2 "
                "ELSE 3 END ASC, "
                "updated_at DESC, created_at DESC, id ASC"
            )
        )
    ).all()
    retained_conversations: set[str] = set()
    detached_trip_ids: list[str] = []
    for trip_id, conversation_id, _status, _updated_at, _created_at in linked_trips:
        if conversation_id in retained_conversations:
            detached_trip_ids.append(trip_id)
        else:
            retained_conversations.add(conversation_id)
    for trip_id in detached_trip_ids:
        await connection.execute(
            text("UPDATE trips SET conversation_id = NULL WHERE id = :trip_id"),
            {"trip_id": trip_id},
        )
    if detached_trip_ids:
        logger.warning(
            "Detached %s duplicate conversation links while preserving every trip",
            len(detached_trip_ids),
        )
    try:
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_trips_conversation_id_unique "
            "ON trips(conversation_id) WHERE conversation_id IS NOT NULL"
        )
    except SQLAlchemyError:
        logger.warning(
            "Existing duplicate conversation-to-trip links were preserved; "
            "the compatibility unique index could not be created"
        )


async def bootstrap_system_admin(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    if not settings.bootstrap_admin_enabled:
        return

    async with app.state.session_factory() as db:
        user = (
            await db.execute(
                select(User).where(
                    User.email == settings.bootstrap_admin_email.lower()
                )
            )
        ).scalar_one_or_none()
        if user is not None:
            if user.role != "admin":
                raise RuntimeError(
                    "BOOTSTRAP_ADMIN_EMAIL belongs to a non-system user"
                )
            tenant = await db.get(Tenant, user.tenant_id)
            if tenant is None:
                raise RuntimeError("Bootstrap administrator tenant is missing")
            # This also safely isolates the exact legacy local admin tenant
            # without deleting any trips, documents, or balance history.
            tenant.is_system = True
            if tenant.name == settings.seed_tenant_name:
                target_name_owner = await db.scalar(
                    select(Tenant.id).where(
                        Tenant.name == settings.bootstrap_admin_tenant_name,
                        Tenant.id != tenant.id,
                    )
                )
                if target_name_owner is None:
                    tenant.name = settings.bootstrap_admin_tenant_name
            await db.commit()
            return

        tenant = await db.scalar(
            select(Tenant).where(
                Tenant.name == settings.bootstrap_admin_tenant_name
            )
        )
        if tenant is not None and not tenant.is_system:
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_TENANT_NAME belongs to a customer tenant"
            )
        if tenant is None:
            tenant = Tenant(
                name=settings.bootstrap_admin_tenant_name,
                is_system=True,
            )
            db.add(tenant)
            await db.flush()

        db.add(
            User(
                tenant_id=tenant.id,
                email=settings.bootstrap_admin_email.lower(),
                full_name="Администратор Buscript",
                password_hash=hash_password(settings.bootstrap_admin_password),
                role="admin",
            )
        )
        await db.commit()


async def seed_demo_profile(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    if not settings.demo_seed_enabled:
        return

    async with app.state.session_factory() as db:
        tenant = await db.scalar(
            select(Tenant).where(Tenant.name == settings.seed_tenant_name)
        )
        if tenant is None:
            tenant = Tenant(name=settings.seed_tenant_name)
            db.add(tenant)
            await db.flush()
        user = await db.scalar(
            select(User).where(User.email == settings.seed_user_email.lower())
        )
        if user is None:
            user = User(
                tenant_id=tenant.id,
                email=settings.seed_user_email.lower(),
                full_name="Демо-пользователь",
                password_hash=hash_password(settings.seed_user_password),
                role="client_admin",
            )
            db.add(user)
            await db.flush()
        account = await db.scalar(
            select(BalanceAccount).where(BalanceAccount.tenant_id == tenant.id)
        )
        if account is None:
            amount = Decimal(settings.seed_notification_balance)
            account = BalanceAccount(tenant_id=tenant.id, available=amount)
            db.add(account)
            await db.flush()
            if amount:
                db.add(
                    BillingTransaction(
                        tenant_id=tenant.id,
                        account_id=account.id,
                        actor_id=user.id,
                        kind="demo_seed_credit",
                        amount=amount,
                        balance_after=amount,
                        note="Явно включённый демо-баланс",
                        idempotency_key=f"demo-seed:{tenant.id}",
                    )
                )
        await db.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate_for_runtime()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(resolved_settings.database_url)
        app.state.settings = resolved_settings
        app.state.engine = engine
        app.state.session_factory = make_session_factory(engine)
        resolved_settings.storage_root.mkdir(parents=True, exist_ok=True)
        if resolved_settings.auto_create_schema:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await _sqlite_compatibility_migrations(connection)
        await bootstrap_system_admin(app)
        await seed_demo_profile(app)
        workflow_tasks: list[asyncio.Task] = []
        try:
            from .automation.workflow import recover_pending_workflow_runs

            workflow_tasks = await recover_pending_workflow_runs(app)
        except ImportError:
            logger.info("Assistant workflow recovery is not installed")
        yield
        for task in workflow_tasks:
            if not task.done():
                task.cancel()
        if workflow_tasks:
            await asyncio.gather(*workflow_tasks, return_exceptions=True)
        await engine.dispose()

    application = FastAPI(
        title="Buscript API",
        description="API платформы автоматизации уведомлений ГИБДД",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id", str(uuid.uuid4()))[:128]
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error", extra={"request_id": request_id})
            raise
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(_request: Request, _exc: SQLAlchemyError):
        logger.exception("Database error")
        return JSONResponse(status_code=503, content={"detail": "Ошибка базы данных"})

    @application.get("/", include_in_schema=False)
    async def root():
        return {"name": "Buscript API", "docs": "/docs"}

    @application.get("/health", tags=["system"])
    async def health():
        return {
            "status": "ok",
            "service": "buscript-api",
            "environment": resolved_settings.app_env,
            "live_submission": resolved_settings.gibdd_live_submission,
        }

    @application.get("/ready", tags=["system"])
    async def ready(request: Request):
        try:
            async with request.app.state.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return {"status": "ready"}
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "not_ready"})

    prefix = resolved_settings.api_prefix
    application.include_router(auth.router, prefix=prefix)
    application.include_router(admin.router, prefix=prefix)
    application.include_router(onboarding.router, prefix=prefix)
    application.include_router(fleet.router, prefix=prefix)
    application.include_router(conversations.router, prefix=prefix)
    application.include_router(trips.router, prefix=prefix)
    application.include_router(files.router, prefix=prefix)
    application.include_router(billing.router, prefix=prefix)
    application.include_router(audit.router, prefix=prefix)

    # Automation routes are optional during isolated backend development.
    try:
        from .automation.router import router as automation_router
        from .automation.workflow_router import router as workflow_router

        application.include_router(automation_router, prefix=prefix)
        application.include_router(workflow_router, prefix=prefix)
    except ImportError:
        logger.info("Automation router is not installed yet")

    return application


app = create_app()
