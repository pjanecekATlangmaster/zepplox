from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.accounts import connection_for, read_connection_secret, utcnow
from app.config import get_settings
from app.intervals import list_activities, summarize_activity
from app.models import ImportLog, SyncRun, User
from app.sync import auto_skip_reason, latest_imports, next_user_sync_at

QUEUE_LIMIT = 40
RUN_WINDOWS = 12


def _naive_utc(when: datetime) -> datetime:
    if when.tzinfo is not None:
        return when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def aggregate_sync_runs(runs: list[SyncRun], interval_minutes: int, *, limit: int = RUN_WINDOWS) -> list[dict[str, object]]:
    interval = max(int(interval_minutes) or 30, 1)
    step = interval * 60
    buckets: dict[int, dict[str, object]] = {}
    order: list[int] = []
    for run in runs:
        ts = run.started_at
        if ts is None:
            continue
        ts = _naive_utc(ts)
        epoch = int(ts.replace(tzinfo=timezone.utc).timestamp())
        window = epoch - (epoch % step)
        if window not in buckets:
            if len(buckets) >= limit:
                continue
            buckets[window] = {
                "started_at": datetime.fromtimestamp(window, tz=timezone.utc).replace(tzinfo=None),
                "users_processed": 0,
                "imported_count": 0,
                "skipped_count": 0,
                "error_count": 0,
            }
            order.append(window)
        bucket = buckets[window]
        bucket["users_processed"] = int(bucket["users_processed"]) + int(run.users_processed or 0)
        bucket["imported_count"] = int(bucket["imported_count"]) + int(run.imported_count or 0)
        bucket["skipped_count"] = int(bucket["skipped_count"]) + int(run.skipped_count or 0)
        bucket["error_count"] = int(bucket["error_count"]) + int(run.error_count or 0)
    return [buckets[key] for key in order]


def _collect_queue(db: Session, users: list[User], *, interval_minutes: int) -> list[dict[str, object]]:
    settings = get_settings()
    newest = utcnow().date()
    oldest = (utcnow() - timedelta(hours=max(settings.sync_lookback_hours, 24))).date()
    queue: list[dict[str, object]] = []
    for user in users:
        prefs = user.settings
        if prefs is None or not prefs.sync_enabled:
            continue
        intervals = connection_for(db, user.id, "intervals")
        if intervals is None:
            continue
        try:
            api_key = read_connection_secret(intervals)
        except Exception as exc:
            queue.append(
                {
                    "email": user.email,
                    "start": "",
                    "name": "",
                    "sport": "",
                    "status": "error",
                    "message": str(exc)[:200],
                    "next_at": next_user_sync_at(user.id, interval_minutes) if interval_minutes else None,
                }
            )
            if len(queue) >= QUEUE_LIMIT:
                return queue
            continue
        try:
            activities = list_activities(api_key, oldest, newest)
        except Exception as exc:
            queue.append(
                {
                    "email": user.email,
                    "start": "",
                    "name": "",
                    "sport": "",
                    "status": "error",
                    "message": f"Intervals.icu: {exc}"[:200],
                    "next_at": next_user_sync_at(user.id, interval_minutes) if interval_minutes else None,
                }
            )
            if len(queue) >= QUEUE_LIMIT:
                return queue
            continue
        logs = latest_imports(db, user.id)
        livelox = connection_for(db, user.id, "livelox")
        next_at = next_user_sync_at(user.id, interval_minutes) if interval_minutes else None
        for activity in activities:
            summary = summarize_activity(activity)
            activity_id = str(summary.get("id") or "")
            if not activity_id:
                continue
            if auto_skip_reason(prefs, activity):
                continue
            previous = logs.get(activity_id)
            if previous is not None and previous.status == "imported":
                continue
            if livelox is None:
                status, message = "error", "Livelox není propojený"
            elif previous is None or previous.status == "skipped":
                status, message = "pending", ""
            else:
                status, message = previous.status, previous.message
            queue.append(
                {
                    "email": user.email,
                    "start": str(summary.get("start") or ""),
                    "name": str(summary.get("name") or activity_id),
                    "sport": str(summary.get("sport") or ""),
                    "status": status,
                    "message": message,
                    "next_at": next_at,
                }
            )
            if len(queue) >= QUEUE_LIMIT:
                return queue
    return queue


def collect_admin_stats(db: Session, *, log_days: int = 7) -> dict[str, object]:
    users = list(
        db.scalars(select(User).options(selectinload(User.settings), selectinload(User.connections))).all()
    )
    since = utcnow() - timedelta(days=log_days)
    week_login = utcnow() - timedelta(days=7)
    interval_minutes = max(int(get_settings().sync_interval_minutes), 0)
    intervals = livelox = both = sync_on = recent_login = 0
    rows: list[dict[str, object]] = []
    for user in users:
        providers = {row.provider for row in user.connections}
        has_intervals = "intervals" in providers
        has_livelox = "livelox" in providers
        enabled = bool(user.settings and user.settings.sync_enabled)
        if has_intervals:
            intervals += 1
        if has_livelox:
            livelox += 1
        if has_intervals and has_livelox:
            both += 1
        if enabled:
            sync_on += 1
        if user.last_login_at and user.last_login_at >= week_login:
            recent_login += 1
        rows.append(
            {
                "email": user.email,
                "intervals": has_intervals,
                "livelox": has_livelox,
                "sync_on": enabled,
                "last_sync": user.settings.last_sync_at if user.settings else None,
                "last_login": user.last_login_at,
                "created": user.created_at,
            }
        )

    status_counts = dict(
        db.execute(
            select(ImportLog.status, func.count())
            .where(ImportLog.created_at >= since, ImportLog.intervals_activity_id != "")
            .group_by(ImportLog.status)
        ).all()
    )
    errors = list(
        db.scalars(
            select(ImportLog)
            .where(ImportLog.status == "error", ImportLog.created_at >= since)
            .order_by(ImportLog.id.desc())
            .limit(15)
        ).all()
    )
    raw_runs = list(db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(500)).all())
    error_emails = {user.id: user.email for user in users}
    queue = _collect_queue(db, users, interval_minutes=interval_minutes)
    return {
        "user_count": len(users),
        "intervals": intervals,
        "livelox": livelox,
        "both": both,
        "sync_on": sync_on,
        "recent_login": recent_login,
        "log_days": log_days,
        "interval_minutes": interval_minutes or 30,
        "lookback_hours": max(int(get_settings().sync_lookback_hours), 24),
        "imported": int(status_counts.get("imported") or 0),
        "skipped": int(status_counts.get("skipped") or 0),
        "errors": int(status_counts.get("error") or 0),
        "queue_count": len(queue),
        "users": rows,
        "queue": queue,
        "error_rows": [
            {
                "when": row.created_at,
                "email": error_emails.get(row.user_id, ""),
                "title": row.title,
                "message": row.message,
            }
            for row in errors
        ],
        "runs": aggregate_sync_runs(raw_runs, interval_minutes or 30),
    }
