from __future__ import annotations

import gzip
import logging
import threading
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
_sync_lock = threading.Lock()

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
MANUAL_LIMIT = 8


def parse_sports(raw: str | None) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def allowed_sports(raw: str | None) -> set[str]:
    chosen = parse_sports(raw) & set(SPORT_CHOICES)
    return chosen


def dump_sports(codes: list[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code in SPORT_CHOICES and code not in seen:
            seen.add(code)
            ordered.append(code)
    return ",".join(ordered)


def _as_fit(payload: bytes) -> bytes:
    if payload[:2] == b"\x1f\x8b":
        return gzip.decompress(payload)
    return payload


def _sports(settings_row: UserSettings) -> set[str]:
    return allowed_sports(settings_row.sports)


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


def _activity_fields(activity: dict) -> tuple[str, str, str, int]:
    activity_id = str(activity.get("id") or "")
    title = str(activity.get("name") or activity_id)
    sport = str(activity.get("type") or "")
    duration = int(activity.get("elapsed_time") or activity.get("moving_time") or 0)
    return activity_id, title, sport, duration


def _previous_import(db: Session, user_id: int, activity_id: str) -> ImportLog | None:
    return db.scalars(
        select(ImportLog)
        .where(ImportLog.user_id == user_id, ImportLog.intervals_activity_id == activity_id)
        .order_by(ImportLog.id.desc())
    ).first()


def latest_imports(db: Session, user_id: int) -> dict[str, ImportLog]:
    rows = db.scalars(
        select(ImportLog)
        .where(ImportLog.user_id == user_id, ImportLog.intervals_activity_id != "")
        .order_by(ImportLog.id.desc())
    ).all()
    found: dict[str, ImportLog] = {}
    for row in rows:
        found.setdefault(row.intervals_activity_id, row)
    return found


def import_one_activity(
    db: Session,
    settings: Settings,
    user: User,
    api_key: str,
    activity: dict,
    *,
    ignore_filters: bool = False,
    skip_if_done: bool = True,
) -> str:
    prefs = user.settings
    activity_id, title, sport, duration = _activity_fields(activity)
    if not activity_id:
        return "skipped"
    if skip_if_done:
        previous = _previous_import(db, user.id, activity_id)
        if previous is not None and previous.status in {"imported", "skipped"}:
            return "done"
    if not ignore_filters and prefs is not None:
        if sport not in _sports(prefs):
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="skipped", message="Sport není zapnutý",
            )
            return "skipped"
        if prefs.require_gps and not activity_has_gps(activity):
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="skipped", message="Aktivita nemá GPS",
            )
            return "skipped"
        if prefs.min_duration_seconds and duration < prefs.min_duration_seconds:
            _log_row(
                db, user.id, activity_id=activity_id, title=title, sport=sport,
                status="skipped", message="Příliš krátká aktivita",
            )
            return "skipped"
    elif not activity_has_gps(activity):
        _log_row(
            db, user.id, activity_id=activity_id, title=title, sport=sport,
            status="error", message="Aktivita nemá GPS",
        )
        return "error"

    try:
        access = _livelox_access(db, settings, user.id)
    except Exception as exc:
        _log_row(
            db, user.id, activity_id=activity_id, title=title, sport=sport,
            status="error", message=f"Livelox token: {exc}",
        )
        return "error"
    if access is None:
        _log_row(
            db, user.id, activity_id=activity_id, title=title, sport=sport,
            status="error", message="Livelox není propojený",
        )
        return "error"

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
        message = "Importováno" if final_status == "imported" else "Odesláno, Livelox ještě zpracovává"
        _log_row(
            db, user.id, activity_id=activity_id, title=title, sport=sport,
            status="imported", message=message, event=event_name,
        )
        return "imported"
    except Exception as exc:
        _log_row(
            db, user.id, activity_id=activity_id, title=title, sport=sport,
            status="error", message=str(exc),
        )
        return "error"


def _intervals_key(db: Session, user: User) -> str | None:
    intervals = connection_for(db, user.id, "intervals")
    if intervals is None:
        return None
    return read_connection_secret(intervals)


def sync_user(db: Session, settings: Settings, user: User) -> tuple[int, int, int]:
    imported = skipped = errors = 0
    prefs = user.settings
    if prefs is None or not prefs.sync_enabled:
        return 0, 0, 0

    try:
        api_key = _intervals_key(db, user)
    except ValueError as exc:
        _log_row(db, user.id, status="error", message=str(exc))
        prefs.last_sync_at = utcnow()
        return 0, 0, 1
    if api_key is None:
        _log_row(db, user.id, status="error", message="Intervals.icu není propojené")
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

    for activity in activities:
        result = import_one_activity(db, settings, user, api_key, activity)
        if result == "imported":
            imported += 1
        elif result == "error":
            errors += 1
        elif result == "skipped":
            skipped += 1

    prefs.last_sync_at = utcnow()
    return imported, skipped, errors


def import_selected(
    db: Session,
    settings: Settings,
    user: User,
    activity_ids: list[str],
) -> tuple[int, int, int]:
    chosen: list[str] = []
    seen: set[str] = set()
    for raw in activity_ids:
        activity_id = str(raw).strip()
        if not activity_id or activity_id in seen:
            continue
        seen.add(activity_id)
        chosen.append(activity_id)
        if len(chosen) >= MANUAL_LIMIT:
            break
    if not chosen:
        return 0, 0, 0

    api_key = _intervals_key(db, user)
    if not api_key:
        raise RuntimeError("intervals_missing")

    newest = utcnow().date()
    oldest = newest - timedelta(days=30)
    activities = list_activities(api_key, oldest, newest)
    by_id = {str(item.get("id") or ""): item for item in activities}
    imported = skipped = errors = 0
    for activity_id in chosen:
        activity = by_id.get(activity_id)
        if activity is None:
            errors += 1
            _log_row(
                db, user.id, activity_id=activity_id,
                status="error", message="Aktivita už v Intervals.icu není v posledních 30 dnech",
            )
            continue
        result = import_one_activity(
            db, settings, user, api_key, activity,
            ignore_filters=True,
            skip_if_done=False,
        )
        if result == "imported":
            imported += 1
        elif result == "error":
            errors += 1
        else:
            skipped += 1
    if user.settings is not None:
        user.settings.last_sync_at = utcnow()
    return imported, skipped, errors


def run_sync() -> None:
    if not _sync_lock.acquire(blocking=False):
        log.warning("sync already running, skip")
        return
    try:
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
    finally:
        _sync_lock.release()


if __name__ == "__main__":
    run_sync()
