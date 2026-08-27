from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.accounts import utcnow
from app.models import ImportLog, SyncRun, User


def collect_admin_stats(db: Session, *, log_days: int = 7) -> dict[str, object]:
    users = list(
        db.scalars(select(User).options(selectinload(User.settings), selectinload(User.connections))).all()
    )
    since = utcnow() - timedelta(days=log_days)
    week_login = utcnow() - timedelta(days=7)
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
    runs = list(db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(10)).all())
    error_emails = {user.id: user.email for user in users}
    return {
        "user_count": len(users),
        "intervals": intervals,
        "livelox": livelox,
        "both": both,
        "sync_on": sync_on,
        "recent_login": recent_login,
        "log_days": log_days,
        "imported": int(status_counts.get("imported") or 0),
        "skipped": int(status_counts.get("skipped") or 0),
        "errors": int(status_counts.get("error") or 0),
        "users": rows,
        "error_rows": [
            {
                "when": row.created_at,
                "email": error_emails.get(row.user_id, ""),
                "title": row.title,
                "message": row.message,
            }
            for row in errors
        ],
        "runs": runs,
    }
