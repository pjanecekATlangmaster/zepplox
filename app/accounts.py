from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt_str, encrypt_str
from app.models import Connection, ImportLog, OtpChallenge, User, UserSettings

DEFAULT_SPORTS = "Run,TrailRun,Walk,Hike"


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str | None:
    value = email.strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        return None
    local, _, domain = value.partition("@")
    if not local or "." not in domain:
        return None
    return value


def recent_otp_count(db: Session, *, email: str | None = None, ip: str | None = None, window: timedelta) -> int:
    since = utcnow() - window
    stmt = select(func.count(OtpChallenge.id)).where(OtpChallenge.created_at >= since)
    if email:
        stmt = stmt.where(OtpChallenge.email == email.lower())
    if ip:
        stmt = stmt.where(OtpChallenge.client_ip == ip)
    return int(db.scalar(stmt) or 0)


def create_otp(db: Session, settings: Settings, email: str, client_ip: str = "") -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utcnow()
    db.add(
        OtpChallenge(
            email=email.lower(),
            code_hash=hash_otp(code),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.otp_ttl_seconds),
            client_ip=client_ip[:64],
        )
    )
    return code


def consume_otp(db: Session, email: str, code: str) -> bool:
    now = utcnow()
    stmt = (
        select(OtpChallenge)
        .where(
            OtpChallenge.email == email.lower(),
            OtpChallenge.code_hash == hash_otp(code.strip()),
            OtpChallenge.consumed_at.is_(None),
            OtpChallenge.expires_at >= now,
        )
        .order_by(OtpChallenge.id.desc())
    )
    row = db.scalars(stmt).first()
    if row is None:
        return False
    row.consumed_at = now
    return True


def get_or_create_user(db: Session, email: str) -> User:
    normalized = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized))
    if user is None:
        user = User(email=normalized)
        db.add(user)
        db.flush()
        db.add(UserSettings(user_id=user.id, sports=DEFAULT_SPORTS, sync_enabled=1))
    return user


def ensure_user_settings(db: Session, user: User) -> UserSettings:
    if user.settings is None:
        user.settings = UserSettings(user_id=user.id, sports=DEFAULT_SPORTS, sync_enabled=1)
        db.add(user.settings)
        db.flush()
    return user.settings


def delete_user_account(db: Session, user: User) -> None:
    email = user.email
    user_id = user.id
    db.execute(delete(ImportLog).where(ImportLog.user_id == user_id))
    db.execute(delete(Connection).where(Connection.user_id == user_id))
    db.execute(delete(UserSettings).where(UserSettings.user_id == user_id))
    db.execute(delete(OtpChallenge).where(OtpChallenge.email == email))
    db.delete(user)


def connection_for(db: Session, user_id: int, provider: str) -> Connection | None:
    return db.scalar(
        select(Connection).where(Connection.user_id == user_id, Connection.provider == provider)
    )


def upsert_connection(db: Session, user_id: int, provider: str, secret: str, extra: str = "") -> None:
    row = connection_for(db, user_id, provider)
    blob = encrypt_str(secret)
    if row is None:
        db.add(Connection(user_id=user_id, provider=provider, secret_encrypted=blob, extra=extra))
        return
    row.secret_encrypted = blob
    row.extra = extra


def read_connection_secret(row: Connection) -> str:
    return decrypt_str(row.secret_encrypted)


def livelox_tokens(row: Connection) -> dict:
    return json.loads(read_connection_secret(row))


def delete_connection(db: Session, user_id: int, provider: str) -> Connection | None:
    row = connection_for(db, user_id, provider)
    if row is None:
        return None
    db.delete(row)
    return row
