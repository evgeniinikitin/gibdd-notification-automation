from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Buscript"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    secret_key: str = Field(default_factory=lambda: secrets.token_urlsafe(48), min_length=32)
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=14, ge=1, le=90)
    database_url: str = "sqlite+aiosqlite:///./buscript.db"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("./storage")
    notification_template_path: Path = (
        Path(__file__).resolve().parents[1]
        / "assets"
        / "templates"
        / "base_notification_unsigned.docx"
    )
    max_upload_size_mb: int = Field(default=50, ge=1, le=250)
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3100,http://127.0.0.1:3100"
    )
    auto_create_schema: bool = True

    # A system administrator is bootstrapped into an internal tenant which is
    # excluded from customer-company lists. This keeps a fresh local install
    # manageable without manufacturing a customer, trips, or paid balance.
    bootstrap_admin_enabled: bool = False
    bootstrap_admin_tenant_name: str = "Buscript Administration"
    bootstrap_admin_email: str = "admin@buscript.dev"
    bootstrap_admin_password: str = ""

    # Optional fixtures are deliberately opt-in. They are useful for isolated
    # demos, but must never appear merely because the API was started.
    demo_seed_enabled: bool = False
    seed_tenant_name: str = "Компания Test"
    seed_user_email: str = "demo@buscript.dev"
    seed_user_password: str = ""
    seed_notification_balance: int = Field(default=25, ge=0)

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_api_key_4: str = ""
    gemini_api_key_5: str = ""
    gemini_api_key_6: str = ""
    gemini_api_key_7: str = ""
    gemini_api_key_8: str = ""
    gemini_api_key_9: str = ""
    gemini_api_key_10: str = ""
    gemini_base_url: str = (
        "https://generativelanguage.googleapis.com/v1beta"
    )
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_proxy_url: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openrouter/free"
    openrouter_captcha_model: str = "openrouter/free"
    openrouter_http_referer: str = "http://localhost:3000"
    openrouter_app_title: str = "Buscript"
    openrouter_proxy_url: str = ""
    llm_max_document_context_chars: int = Field(
        default=180_000, ge=20_000, le=1_000_000
    )
    llm_max_chars_per_document: int = Field(
        default=80_000, ge=5_000, le=500_000
    )
    llm_max_recent_message_chars: int = Field(
        default=30_000, ge=5_000, le=200_000
    )
    llm_workflow_timeout_seconds: int = Field(default=120, ge=30, le=600)

    def gemini_api_keys(self) -> tuple[str, ...]:
        """Return configured Gemini keys in priority order, without duplicates."""

        configured = (
            self.gemini_api_key,
            self.gemini_api_key_2,
            self.gemini_api_key_3,
            self.gemini_api_key_4,
            self.gemini_api_key_5,
            self.gemini_api_key_6,
            self.gemini_api_key_7,
            self.gemini_api_key_8,
            self.gemini_api_key_9,
            self.gemini_api_key_10,
        )
        return tuple(dict.fromkeys(key.strip() for key in configured if key.strip()))

    gibdd_base_url: str = (
        "https://xn--80aebkobnwfcnsfk1e0h.xn--p1ai/transportation"
    )
    gibdd_live_submission: bool = False
    gibdd_headful: bool = True
    gibdd_route_direct: bool = True
    captcha_mode: str = "llm_ocr"
    captcha_max_attempts: int = Field(default=5, ge=1, le=15)
    captcha_confidence_threshold: float = Field(default=0.8, ge=0, le=1)

    @field_validator("storage_root", "notification_template_path", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    def validate_for_runtime(self) -> None:
        if self.app_env.lower() == "production" and not os.environ.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be explicitly set in production")
        if (
            self.app_env.lower() == "production"
            and self.bootstrap_admin_enabled
            and not self.bootstrap_admin_password
        ):
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_PASSWORD must be changed or "
                "BOOTSTRAP_ADMIN_ENABLED must be disabled in production"
            )
        if self.app_env.lower() == "production" and self.demo_seed_enabled:
            raise RuntimeError("DEMO_SEED_ENABLED must be false in production")
        if (
            self.app_env.lower() == "production"
            and self.llm_provider.strip().lower() in {"mock", "rule_based"}
        ):
            raise RuntimeError("Mock/rule-based LLM provider is forbidden in production")
        from .demo_template import ensure_demo_template

        ensure_demo_template(self.notification_template_path)
        if not self.notification_template_path.is_file():
            raise RuntimeError(
                "System notification template is missing: "
                f"{self.notification_template_path}"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
