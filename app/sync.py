from __future__ import annotations

import gzip
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.accounts import connection_for, livelox_tokens, read_connection_secret, upsert_connection, utcnow
from app.config import Settings, get_settings
from app.db import init_db, session_scope
from app.intervals import activity_has_gps, download_fit, list_activities
from app.livelox import dump_tokens, import_route, import_status, refresh_access_token, tokens_need_refresh
from app.models import ImportLog, SyncRun, User, UserSettings

log = logging.getLogger("zepplox.sync")

SPORT_CHOICES = [
    "Run",
    "TrailRun",
    "Walk",
    "Hike",
    "Ride",
    "MountainBikeRide",
    "Swim",
    "Workout",
    "VirtualRide",
    "VirtualRun",
]


def _as_fit(payload: bytes) -> bytes:
    if payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload)
    return payload


def _sports(settings_row: UserSettings) -> set[str]:
    return {part.strip() for part in settings_row.sports.split(",") if part.strip()}


def _log_row(
    db: Session,
    user_id: int,
    *,
    activity_id: str = "",
    title: str = "",
    sport: str = "",
    status: str,
    message: str = "",
    event: str = "",
) -> None:
    db.add(
        ImportLog(
            user_id=user_id,
            intervals_activity_id=activity_id,
            title=title,
            sport=sport,
            status=status,
            message=message[:500],
            livelox_event=event[:300],
        )
    )


def _livelox_access(db: Session, settings: Settings, user_id: int) -> str | None:
    row = connection_for(db, user_id, "livelox")
    if row is None:
        return None
    tokens = livelox_tokens(row)
    if tokens_need_refresh(tokens):
        tokens = refresh_access_token(settings, tokens["refresh_token"])
        upsert_connection(db, user_id, "livelox", dump_tokens(tokens))
        db.flush()
    return tokens["access_token"]


def purge_old_logs(db: Session, settings: Settings) -> None:
    cutoff = utcnow() - timedelta(days=settings.log_retention_days)
    db.execute(delete(ImportLog).where(ImportLog.created_at < cutoff))
    db.execute(delete(SyncRun).where(SyncRun.started_at < cutoff))


def sync_user(db: Session, settings: Settings, user: User) -> tuple[int, int, int]:
    imported = skipped = errors = 0
    prefs = user.settings
    if prefs is None or not prefs.sync_enabled:
        return 0, 0, 0

    intervals = connection_for(db, user.id, "intervals")
    if intervals is None:
        _log_row(db, user.id, status="error", message="Intervals.icu není propojené")
        prefs.last_sync_at = utcnow()
        return 0, 0, 1

    try:
        api_key = read_connection_secret(intervals)
    except ValueError as exc:
        _log_row(db, user.id, status="error", message=str(exc))
        prefs.last_sync_at = utcnow()
        return 0, 0, 1

    newest = utcnow().date()
    oldest = (utcnow() - timedelta(hours=settings.sync_lookback_hours + 24)).date()
    try:
        activities = list_activities(api_key, oldest, newest)
    except Exception as exc:
        _log_row(db, user.id, status="error", message=f"Intervals.icu: {exc}")
        prefs.last_sync_at = utcnow()
        return 0, 0, 1

    allowed = _sports(prefs)
    for activity in activities:
        activity_id = str(activity.get("id") or "")
        title = str(activity.get("name") or activity_id)
        sport = str(activity.get("type") or "")
        duration = int(activity.get("elapsed_time") or activity.get("moving_time") or 0)

        if not activity_id:
            continue
        previous = db.scalars(
            select(ImportLog)
            .where(ImportLog.user_id == user.id, ImportLog.intervals_activity_id == activity_id)
            .order_by(ImportLog.id.desc())
        ).first()
        if previous is not None and previous.status in {"imported", "skipped"}:
            continue
        if sport not in allowed:
            skipped += 1
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="skipped", message="Sport není zapnutý",
            )
            continue
        if prefs.require_gps and not activity_has_gps(activity):
            skipped += 1
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="skipped", message="Aktivita nemá GPS",
            )
            continue
        if prefs.min_duration_seconds and duration < prefs.min_duration_seconds:
            skipped += 1
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="skipped", message="Příliš krátká aktivita",
            )
            continue

        try:
            access = _livelox_access(db, settings, user.id)
        except Exception as exc:
            errors += 1
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="error", message=f"Livelox token: {exc}",
            )
            continue
        if access is None:
            errors += 1
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="error", message="Livelox není propojený",
            )
            continue

        try:
            fit = _as_fit(download_fit(api_key, activity_id))
            posted = import_route(access, f"zepplox-{activity_id}"[:48], fit)
            route_id = str(posted.get("id") or f"zepplox-{activity_id}")
            event_name = ""
            final_status = "pending"
            for _ in range(8):
                time.sleep(2)
                meta = import_status(access, route_id)
                final_status = str(meta.get("status") or "pending")
                if final_status == "imported":
                    event_name = " / ".join(
                        part
                        for part in (meta.get("eventName"), meta.get("className"), meta.get("viewerUrl"))
                        if part
                    )
                    break
                if final_status == "error":
                    raise RuntimeError(meta.get("errorMessage") or "Livelox import error")
            imported += 1
            message = "Importováno" if final_status == "imported" else "Odesláno, Livelox ještě zpracovává"
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="imported", message=message, event=event_name,
            )
        except Exception as exc:
            errors += 1
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="error", message=str(exc),
            )

    prefs.last_sync_at = utcnow()
    return imported, skipped, errors


def run_sync() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings().require_runtime_secrets()
    init_db()
    run = SyncRun()
    with session_scope() as db:
        db.add(run)
        db.flush()
        purge_old_logs(db, settings)
        users = list(db.scalars(select(User).options(selectinload(User.settings))).all())
        imported = skipped = errors = 0
        processed = 0
        for user in users:
            if user.settings is None:
                continue
            processed += 1
            i, s, e = sync_user(db, settings, user)
            imported += i
            skipped += s
            errors += e
        run.users_processed = processed
        run.imported_count = imported
        run.skipped_count = skipped
        run.error_count = errors
        run.finished_at = utcnow()
        run.note = f"zkontrolováno {processed} uživatelů"
        log.info(
            "sync finished users=%s imported=%s skipped=%s errors=%s",
            processed, imported, skipped, errors,
        )


if __name__ == "__main__":
    run_sync()
