from __future__ import annotations

from functools import lru_cache
from typing import Self
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENCRYPTION_ALIASES = {
    "starttls": "starttls",
    "tls": "starttls",
    "ssl": "ssl",
    "smtps": "ssl",
    "none": "none",
    "off": "none",
    "false": "none",
    "0": "none",
}


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
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_encryption: str = "starttls"
    smtp_from: str = ""
    smtp_from_name: str = "ZeppLox"

    livelox_client_id: str = ""
    livelox_redirect_uri: str = ""

    sync_lookback_hours: int = 2
    sync_interval_minutes: int = 0
    sync_user_gap_seconds: float = 2.0
    log_retention_days: int = 7
    otp_ttl_seconds: int = 600
    otp_max_per_window: int = 3
    otp_window_seconds: int = 900
    otp_max_per_ip: int = 10
    admin_emails: str = ""

    port: int = Field(default=8456, validation_alias=AliasChoices("PORT", "port"))

    @field_validator("app_base_url", "livelox_redirect_uri")
    @classmethod
    def strip_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("db_host", "db_name", "db_user", "db_password", "smtp_host", "smtp_user", "smtp_password", "smtp_from")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("smtp_encryption", mode="before")
    @classmethod
    def normalize_smtp_encryption(cls, value: object) -> str:
        if value is None or value == "":
            return "starttls"
        raw = str(value).strip().lower()
        if raw in {"true", "1", "yes"}:
            return "starttls"
        mapped = _ENCRYPTION_ALIASES.get(raw)
        if mapped is None:
            raise ValueError("SMTP_ENCRYPTION must be starttls, ssl, or none")
        return mapped

    @model_validator(mode="before")
    @classmethod
    def smtp_starttls_alias(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        encryption = data.get("smtp_encryption") or data.get("SMTP_ENCRYPTION")
        starttls = data.get("smtp_starttls", data.get("SMTP_STARTTLS"))
        if (encryption is None or encryption == "") and starttls is not None:
            flag = str(starttls).strip().lower() in {"1", "true", "yes", "on"}
            data = dict(data)
            data["smtp_encryption"] = "starttls" if flag else "none"
        return data

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def smtp_is_console(self) -> bool:
        return self.smtp_host.lower() in {"console", "log", "stdout"}

    @property
    def smtp_uses_auth(self) -> bool:
        return bool(self.smtp_user)

    @property
    def livelox_callback(self) -> str:
        return self.livelox_redirect_uri or f"{self.app_base_url}/oauth/livelox/callback"

    @property
    def livelox_configured(self) -> bool:
        return bool(self.livelox_client_id)

    @property
    def admin_email_set(self) -> set[str]:
        return {part.strip().lower() for part in self.admin_emails.split(",") if part.strip()}

    def require_runtime_secrets(self) -> Self:
        missing = [
            name
            for name, ok in (
                ("APP_ENCRYPTION_KEY", bool(self.app_encryption_key)),
                ("SESSION_SECRET", bool(self.session_secret)),
                ("DB_PASSWORD", bool(self.db_password)),
                ("SMTP_HOST", bool(self.smtp_host)),
                ("SMTP_FROM", bool(self.smtp_from) or self.smtp_is_console),
                ("SMTP_PASSWORD", bool(self.smtp_password) or not self.smtp_uses_auth),
            )
            if not ok
        ]
        if missing:
            raise RuntimeError("Missing required configuration: " + ", ".join(missing))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
