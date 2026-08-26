from __future__ import annotations

from functools import lru_cache
from typing import Self
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ZeppLox"
    app_base_url: str = "http://127.0.0.1:8456"
    app_encryption_key: str = ""
    session_secret: str = ""

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "zepplox"
    db_user: str = "zepplox"
    db_password: str = ""

    smtp_host: str = ""
    smtp_port: int = 25
    smtp_starttls: bool = True
    smtp_from: str = ""
    smtp_from_name: str = "ZeppLox"

    allowed_emails: str = ""

    livelox_client_id: str = ""
    livelox_redirect_uri: str = ""

    sync_lookback_hours: int = 2
    log_retention_days: int = 7
    otp_ttl_seconds: int = 600
    otp_max_per_window: int = 3
    otp_window_seconds: int = 900

    port: int = Field(default=8456, validation_alias=AliasChoices("PORT", "port"))

    @field_validator("app_base_url", "livelox_redirect_uri")
    @classmethod
    def strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def allowed_email_set(self) -> set[str]:
        return {part.strip().lower() for part in self.allowed_emails.split(",") if part.strip()}

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def livelox_callback(self) -> str:
        return self.livelox_redirect_uri or f"{self.app_base_url}/oauth/livelox/callback"

    @property
    def livelox_configured(self) -> bool:
        return bool(self.livelox_client_id)

    def require_runtime_secrets(self) -> Self:
        missing = [
            name
            for name, ok in (
                ("APP_ENCRYPTION_KEY", bool(self.app_encryption_key)),
                ("SESSION_SECRET", bool(self.session_secret)),
                ("DB_PASSWORD", bool(self.db_password)),
                ("SMTP_HOST", bool(self.smtp_host)),
                ("SMTP_FROM", bool(self.smtp_from)),
                ("ALLOWED_EMAILS", bool(self.allowed_email_set)),
            )
            if not ok
        ]
        if missing:
            raise RuntimeError("Missing required configuration: " + ", ".join(missing))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
