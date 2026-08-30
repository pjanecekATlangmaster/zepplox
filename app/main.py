from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.accounts import (
    consume_otp,
    create_otp,
    delete_connection,
    delete_user_account,
    ensure_user_settings,
    get_or_create_user,
    livelox_tokens,
    normalize_email,
    recent_otp_count,
    read_connection_secret,
    upsert_connection,
    utcnow,
    connection_for,
    DEFAULT_SPORTS,
)
from app.config import get_settings
from app.db import get_db, init_db
from app.i18n import COOKIE as LANG_COOKIE
from app.i18n import LANGS, resolve_lang, strings_for
from app.intervals import (
    IntervalsAuthError,
    PREVIEW_DAYS,
    athlete_display_name,
    get_athlete,
    list_activities,
    summarize_activity,
)
from app.livelox import (
    LiveloxOAuthError,
    authorize_url,
    dump_tokens,
    exchange_code,
    fetch_userinfo_name,
    new_pkce,
    revoke_stored,
)
from app.mail import send_otp_email
from app.models import User
from app.stats import collect_admin_stats
from app.sync import (
    MANUAL_LIMIT,
    SPORT_CHOICES,
    dump_sports,
    import_selected,
    latest_imports,
    mark_next_sync,
    next_user_sync_at,
    parse_sports,
    run_sync,
    schedule_state,
    user_is_due,
)

log = logging.getLogger("zepplox")

ROOT = Path(__file__).resolve().parent
GITHUB_URL = "https://github.com/pjanecekATlangmaster/zepplox"
CSS_VERSION = str(int((ROOT / "static" / "style.css").stat().st_mtime))
settings = get_settings()
templates = Jinja2Templates(directory=str(ROOT / "templates"))


def utc_time_html(when: datetime | None) -> Markup:
    if when is None:
        return Markup("—")
    naive = _utc_naive(when)
    iso = naive.strftime("%Y-%m-%dT%H:%M:%SZ")
    fallback = naive.strftime("%Y-%m-%d %H:%M UTC")
    return Markup(f'<time class="js-local-time" datetime="{iso}">{escape(fallback)}</time>')


templates.env.globals["utc_time"] = utc_time_html


async def _scheduled_sync_loop(minutes: int) -> None:
    first_wait = min(120, max(minutes, 1) * 60)
    tick = 60
    mark_next_sync(utcnow() + timedelta(seconds=first_wait))
    log.info(
        "Scheduled sync every %s min in per-user slots; first tick in %s s, then every %s s",
        minutes, first_wait, tick,
    )
    try:
        await asyncio.sleep(first_wait)
        while True:
            mark_next_sync(None, running=True)
            try:
                await asyncio.to_thread(run_sync, due_only=True)
            except Exception:
                log.exception("Scheduled sync failed")
            mark_next_sync(utcnow() + timedelta(seconds=tick))
            await asyncio.sleep(tick)
    except asyncio.CancelledError:
        mark_next_sync(None)
        log.info("Scheduled sync stopped")
        raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runtime = get_settings().require_runtime_secrets()
    init_db()
    task = None
    if runtime.sync_interval_minutes > 0:
        task = asyncio.create_task(_scheduled_sync_loop(runtime.sync_interval_minutes))
    else:
        log.info("Scheduled sync is off (SYNC_INTERVAL_MINUTES=0)")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "dev-only-change-me",
    session_cookie="zepplox",
    same_site="lax",
    https_only=settings.app_base_url.startswith("https://"),
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def _check_csrf(request: Request, csrf: str) -> None:
    if not csrf or csrf != request.session.get("csrf"):
        raise HTTPException(status_code=400, detail=strings_for(resolve_lang(request))["error_csrf"])


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.scalar(
        select(User).options(selectinload(User.settings)).where(User.id == int(user_id))
    )


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.email.lower() not in settings.admin_email_set:
        raise HTTPException(status_code=404)
    return user


def request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def _ctx(request: Request, **extra):
    lang = extra.pop("lang", None) or resolve_lang(request)
    user = extra.get("user")
    is_admin = bool(
        user is not None
        and getattr(user, "email", None)
        and user.email.lower() in settings.admin_email_set
    )
    return {
        "request": request,
        "app_name": settings.app_name,
        "github_url": GITHUB_URL,
        "csrf": _csrf(request),
        "lang": lang,
        "t": strings_for(lang),
        "is_admin": is_admin,
        "css_version": CSS_VERSION,
        **extra,
    }


def _sport_choices(t: dict[str, str]) -> list[tuple[str, str]]:
    return [(code, t.get(f"sport_{code}", code)) for code in SPORT_CHOICES]


def _selected_sports(user: User) -> set[str]:
    raw = user.settings.sports if user.settings is not None else DEFAULT_SPORTS
    return parse_sports(raw) & set(SPORT_CHOICES)


def _utc_naive(when: datetime) -> datetime:
    if when.tzinfo is not None:
        return when.astimezone(timezone.utc).replace(tzinfo=None)
    return when


def _format_sync_when(when: datetime, lang: str) -> Markup:
    naive = _utc_naive(when)
    iso = naive.strftime("%Y-%m-%dT%H:%M:%SZ")
    if lang == "cs":
        fallback = f"{naive.day}. {naive.month}. {naive.year} {naive.hour:02d}:{naive.minute:02d} UTC"
    else:
        fallback = naive.strftime("%d %b %Y %H:%M UTC")
    return Markup(f'<time class="js-local-time" datetime="{iso}">{escape(fallback)}</time>')


def _sync_schedule_message(lang: str, user_enabled: bool, user_id: int = 0) -> str:
    t = strings_for(lang)
    state = schedule_state()
    minutes = int(state["interval_minutes"] or 0)
    if not state["host_enabled"]:
        return t["sync_host_off"]
    if state["running"] and user_id and user_is_due(user_id, minutes):
        return t["sync_running"].format(minutes=minutes)
    when = next_user_sync_at(user_id, minutes) if user_id else state["next_at"]
    when_label = _format_sync_when(when, lang) if isinstance(when, datetime) else t["sync_when_unknown"]
    if not user_enabled:
        return Markup(t["sync_next_skipped"]).format(when=when_label, minutes=minutes)
    return Markup(t["sync_next"]).format(when=when_label, minutes=minutes)


def _attach_import_status(db: Session, user_id: int, activities: list[dict]) -> None:
    logs = latest_imports(db, user_id)
    for item in activities:
        row = logs.get(str(item.get("id") or ""))
        item["import_status"] = row.status if row else ""
        item["import_message"] = row.message if row else ""
        item["import_event"] = row.livelox_event if row else ""


def _intervals_connected(db: Session, user_id: int):
    return connection_for(db, user_id, "intervals")


def _livelox_connected(db: Session, user_id: int):
    return connection_for(db, user_id, "livelox")


def _revoke_livelox(db: Session, user_id: int) -> None:
    row = _livelox_connected(db, user_id)
    if row is None:
        return
    try:
        revoke_stored(settings, livelox_tokens(row))
    except Exception:
        log.exception("Failed to revoke Livelox tokens for user %s", user_id)


def _load_preview(db: Session, user_id: int, t: dict[str, str]):
    row = _intervals_connected(db, user_id)
    if row is None:
        return None, "", [], None
    name = row.extra or "Intervals.icu"
    try:
        api_key = read_connection_secret(row)
        newest = utcnow().date()
        oldest = newest - timedelta(days=PREVIEW_DAYS)
        raw = list_activities(api_key, oldest, newest)
        activities = [summarize_activity(item) for item in raw]
        _attach_import_status(db, user_id, activities)
        return row, name, activities, None
    except IntervalsAuthError:
        return row, name, [], t["intervals_bad_key"]
    except Exception:
        log.exception("Failed to list Intervals.icu activities for user %s", user_id)
        return row, name, [], t["intervals_error"]


def _html(request: Request, template: str, status_code: int = 200, **extra) -> HTMLResponse:
    lang = resolve_lang(request)
    response = templates.TemplateResponse(
        request,
        template,
        _ctx(request, lang=lang, **extra),
        status_code=status_code,
    )
    query_lang = (request.query_params.get("lang") or "").lower()
    if query_lang in LANGS:
        response.set_cookie(
            LANG_COOKIE,
            query_lang,
            max_age=365 * 24 * 3600,
            samesite="lax",
            secure=settings.app_base_url.startswith("https://"),
            path="/",
        )
    return response


@app.api_route("/healthz", methods=["GET", "HEAD"])
def healthz(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    user: User | None = Depends(current_user),
    db: Session = Depends(get_db),
):
    if user is None:
        return _html(request, "landing.html")
    t = strings_for(resolve_lang(request))
    _row, intervals_name, activities, fetch_error = _load_preview(db, user.id, t)
    livelox = _livelox_connected(db, user.id)
    error_key = request.query_params.get("error") or ""
    error = t.get(error_key) if error_key in t else None
    notice = request.query_params.get("notice")
    return _html(
        request,
        "home.html",
        user=user,
        intervals_name=intervals_name,
        intervals_connected=bool(_row),
        livelox_name=(livelox.extra if livelox else "") or "Livelox",
        livelox_connected=livelox is not None,
        livelox_configured=settings.livelox_configured,
        activities=activities,
        fetch_error=fetch_error,
        preview_days=PREVIEW_DAYS,
        can_send=livelox is not None,
        sync_enabled=user.settings is None or bool(user.settings.sync_enabled),
        sync_schedule=_sync_schedule_message(
            resolve_lang(request),
            user.settings is None or bool(user.settings.sync_enabled),
            user.id,
        ),
        manual_limit=MANUAL_LIMIT,
        notice=notice,
        error=error,
        sent_ok=request.query_params.get("ok") or "0",
        sent_err=request.query_params.get("err") or "0",
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return _html(request, "login.html", error=None)


@app.post("/login")
def login_start(
    request: Request,
    email: str = Form(...),
    csrf: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    t = strings_for(resolve_lang(request))
    normalized = normalize_email(email)
    if not normalized:
        return _html(request, "login.html", status_code=400, error=t["error_email"])
    window = timedelta(seconds=settings.otp_window_seconds)
    ip = request_ip(request)
    if recent_otp_count(db, email=normalized, window=window) >= settings.otp_max_per_window:
        return _html(request, "login.html", status_code=429, error=t["error_otp_email"])
    if ip and recent_otp_count(db, ip=ip, window=window) >= settings.otp_max_per_ip:
        return _html(request, "login.html", status_code=429, error=t["error_otp_ip"])
    code = create_otp(db, settings, normalized, client_ip=ip)
    returning = db.scalar(select(User.id).where(User.email == normalized)) is not None
    try:
        send_otp_email(
            settings,
            normalized,
            code,
            lang=resolve_lang(request),
            returning=returning,
        )
    except Exception:
        log.exception("Failed to send OTP to %s", normalized)
        return _html(request, "login.html", status_code=502, error=t["error_smtp"])
    request.session["otp_email"] = normalized
    return RedirectResponse("/login/verify", status_code=303)


@app.get("/login/verify", response_class=HTMLResponse)
def verify_form(request: Request):
    if not request.session.get("otp_email"):
        return RedirectResponse("/login", status_code=303)
    return _html(request, "verify.html", error=None)


@app.post("/login/verify")
def verify_post(
    request: Request,
    code: str = Form(...),
    csrf: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    email = request.session.get("otp_email")
    if not email:
        return RedirectResponse("/login", status_code=303)
    if not consume_otp(db, email, code):
        return _html(
            request,
            "verify.html",
            status_code=400,
            error=strings_for(resolve_lang(request))["error_otp"],
        )
    user = get_or_create_user(db, email)
    user.last_login_at = utcnow()
    request.session.clear()
    request.session["user_id"] = user.id
    _csrf(request)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request, csrf: str = Form(...)):
    _check_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _settings_html(
    request: Request,
    user: User,
    db: Session,
    *,
    status_code: int = 200,
    notice: str | None = None,
    error: str | None = None,
):
    intervals = _intervals_connected(db, user.id)
    livelox = _livelox_connected(db, user.id)
    return _html(
        request,
        "settings.html",
        status_code=status_code,
        user=user,
        sync_enabled=user.settings is None or bool(user.settings.sync_enabled),
        sync_schedule=_sync_schedule_message(
            resolve_lang(request),
            user.settings is None or bool(user.settings.sync_enabled),
            user.id,
        ),
        intervals_name=intervals.extra if intervals else "",
        intervals_connected=intervals is not None,
        livelox_name=(livelox.extra if livelox else "") or "Livelox",
        livelox_connected=livelox is not None,
        livelox_configured=settings.livelox_configured,
        livelox_callback=settings.livelox_callback,
        selected_sports=_selected_sports(user),
        sport_choices=_sport_choices(strings_for(resolve_lang(request))),
        notice=notice,
        error=error,
    )


@app.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    stats = collect_admin_stats(db, log_days=settings.log_retention_days)
    return _html(request, "admin.html", user=user, stats=stats)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    t = strings_for(resolve_lang(request))
    error_key = request.query_params.get("error") or ""
    error = None
    if error_key in t:
        error = t[error_key]
        if "{callback}" in error:
            error = error.format(callback=settings.livelox_callback)
    return _settings_html(request, user, db, notice=request.query_params.get("notice"), error=error)


@app.post("/settings/intervals")
def settings_intervals(
    request: Request,
    csrf: str = Form(...),
    api_key: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    t = strings_for(resolve_lang(request))
    key = api_key.strip()
    if not key:
        return _settings_html(request, user, db, status_code=400, error=t["intervals_need_key"])
    try:
        name = athlete_display_name(get_athlete(key))
        upsert_connection(db, user.id, "intervals", key, extra=name)
    except IntervalsAuthError:
        return _settings_html(request, user, db, status_code=400, error=t["intervals_bad_key"])
    except Exception:
        log.exception("Failed to verify Intervals.icu key for user %s", user.id)
        return _settings_html(request, user, db, status_code=502, error=t["intervals_error"])
    return RedirectResponse("/", status_code=303)


@app.post("/settings/intervals/disconnect")
def settings_intervals_disconnect(
    request: Request,
    csrf: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    delete_connection(db, user.id, "intervals")
    return RedirectResponse("/settings?notice=intervals_gone", status_code=303)


@app.post("/settings/sync")
def settings_sync(
    request: Request,
    csrf: str = Form(...),
    enabled: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    row = ensure_user_settings(db, user)
    row.sync_enabled = 1 if enabled == "1" else 0
    notice = "sync_on" if row.sync_enabled else "sync_off"
    return RedirectResponse(f"/settings?notice={notice}", status_code=303)


@app.post("/settings/sports")
async def settings_sports(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _check_csrf(request, str(form.get("csrf") or ""))
    row = ensure_user_settings(db, user)
    row.sports = dump_sports([str(value) for value in form.getlist("sport")])
    return RedirectResponse("/settings?notice=sports_saved", status_code=303)


@app.post("/activities/send")
async def activities_send(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    form = await request.form()
    _check_csrf(request, str(form.get("csrf") or ""))
    ids = [str(item).strip() for item in form.getlist("activity_id") if str(item).strip()]
    if not ids:
        return RedirectResponse("/?error=manual_none", status_code=303)
    if _livelox_connected(db, user.id) is None:
        return RedirectResponse("/?error=manual_livelox", status_code=303)
    if _intervals_connected(db, user.id) is None:
        return RedirectResponse("/?error=manual_intervals", status_code=303)
    try:
        imported, _skipped, errors = import_selected(db, settings, user, ids)
    except IntervalsAuthError:
        return RedirectResponse("/?error=intervals_bad_key", status_code=303)
    except Exception:
        log.exception("Manual Livelox send failed for user %s", user.id)
        return RedirectResponse("/?error=manual_failed", status_code=303)
    return RedirectResponse(f"/?notice=manual_ok&ok={imported}&err={errors}", status_code=303)


@app.post("/settings/delete")
def settings_delete(
    request: Request,
    csrf: str = Form(...),
    confirm: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    t = strings_for(resolve_lang(request))
    if confirm != "delete":
        return _settings_html(request, user, db, status_code=400, error=t["delete_need_confirm"])
    _revoke_livelox(db, user.id)
    delete_user_account(db, user)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.post("/oauth/livelox/start")
def livelox_start(
    request: Request,
    csrf: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    t = strings_for(resolve_lang(request))
    if not settings.livelox_configured:
        return _settings_html(
            request,
            user,
            db,
            status_code=400,
            error=t["livelox_not_configured"].format(callback=settings.livelox_callback),
        )
    verifier, challenge = new_pkce()
    state = secrets.token_urlsafe(24)
    request.session["livelox_oauth"] = {"state": state, "verifier": verifier}
    return RedirectResponse(authorize_url(settings, state, challenge), status_code=303)


@app.get("/oauth/livelox/callback")
def livelox_callback(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    pending = request.session.pop("livelox_oauth", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if error:
        return RedirectResponse("/settings?error=livelox_denied", status_code=303)
    if not isinstance(pending, dict) or pending.get("state") != state or not code:
        return RedirectResponse("/settings?error=livelox_state", status_code=303)
    try:
        tokens = exchange_code(settings, code, pending["verifier"])
        name = fetch_userinfo_name(tokens["access_token"])
        upsert_connection(db, user.id, "livelox", dump_tokens(tokens), extra=name)
    except LiveloxOAuthError:
        log.exception("Livelox token exchange failed for user %s", user.id)
        return RedirectResponse("/settings?error=livelox_token", status_code=303)
    except Exception:
        log.exception("Failed to store Livelox tokens for user %s", user.id)
        return RedirectResponse("/settings?error=livelox_token", status_code=303)
    return RedirectResponse("/settings?notice=livelox_ok", status_code=303)


@app.post("/settings/livelox/disconnect")
def settings_livelox_disconnect(
    request: Request,
    csrf: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    _revoke_livelox(db, user.id)
    delete_connection(db, user.id, "livelox")
    return RedirectResponse("/settings?notice=livelox_gone", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return _html(request, "privacy.html")


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    raise exc
