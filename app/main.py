from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.accounts import (
    connection_for,
    consume_otp,
    create_otp,
    email_allowed,
    get_or_create_user,
    recent_otp_count,
    upsert_connection,
    utcnow,
)
from app.config import get_settings
from app.db import get_db, init_db
from app.livelox import authorize_url, dump_tokens, exchange_code, new_pkce
from app.mail import send_otp_email
from app.models import ImportLog, SyncRun, User, UserSettings
from app.sync import SPORT_CHOICES

ROOT = Path(__file__).resolve().parent
settings = get_settings()
templates = Jinja2Templates(directory=str(ROOT / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings().require_runtime_secrets()
    init_db()
    yield


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
        raise HTTPException(status_code=400, detail="Neplatný formulář, zkuste to znovu.")


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


def _ctx(request: Request, **extra):
    return {
        "request": request,
        "app_name": settings.app_name,
        "csrf": _csrf(request),
        **extra,
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    last_run = db.scalars(select(SyncRun).order_by(SyncRun.id.desc())).first()
    logs = list(
        db.scalars(
            select(ImportLog)
            .where(ImportLog.user_id == user.id)
            .order_by(ImportLog.id.desc())
            .limit(50)
        ).all()
    )
    intervals = connection_for(db, user.id, "intervals")
    livelox = connection_for(db, user.id, "livelox")
    return templates.TemplateResponse(
        request,
        "home.html",
        _ctx(
            request,
            user=user,
            last_run=last_run,
            logs=logs,
            intervals_ok=intervals is not None,
            livelox_ok=livelox is not None,
            sync_on=bool(user.settings and user.settings.sync_enabled),
        ),
    )


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", _ctx(request, error=None))


@app.post("/login")
def login_start(
    request: Request,
    email: str = Form(...),
    csrf: str = Form(...),
    db: Session = Depends(get_db),
):
    _check_csrf(request, csrf)
    normalized = email.strip().lower()
    if not email_allowed(settings, normalized):
        return templates.TemplateResponse(
            request,
            "login.html",
            _ctx(request, error="Tento e-mail není na seznamu povolených adres."),
            status_code=403,
        )
    window = timedelta(seconds=settings.otp_window_seconds)
    if recent_otp_count(db, normalized, window) >= settings.otp_max_per_window:
        return templates.TemplateResponse(
            request,
            "login.html",
            _ctx(request, error="Příliš mnoho pokusů. Zkuste to za chvíli."),
            status_code=429,
        )
    code = create_otp(db, settings, normalized)
    send_otp_email(settings, normalized, code)
    request.session["otp_email"] = normalized
    return RedirectResponse("/login/verify", status_code=303)


@app.get("/login/verify", response_class=HTMLResponse)
def verify_form(request: Request):
    if not request.session.get("otp_email"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "verify.html", _ctx(request, error=None))


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
        return templates.TemplateResponse(
            request,
            "verify.html",
            _ctx(request, error="Neplatný nebo prošlý kód."),
            status_code=400,
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


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    prefs = user.settings or UserSettings(user_id=user.id)
    selected = {part.strip() for part in prefs.sports.split(",") if part.strip()}
    return templates.TemplateResponse(
        request,
        "settings.html",
        _ctx(
            request,
            user=user,
            prefs=prefs,
            sports=SPORT_CHOICES,
            selected=selected,
            intervals_ok=connection_for(db, user.id, "intervals") is not None,
            livelox_ok=connection_for(db, user.id, "livelox") is not None,
            livelox_ready=settings.livelox_configured,
            notice=request.query_params.get("notice"),
        ),
    )


@app.post("/settings")
def settings_save(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    csrf: str = Form(...),
    sync_enabled: str | None = Form(None),
    require_gps: str | None = Form(None),
    min_duration_seconds: int = Form(0),
    sports: list[str] = Form(default=[]),
    intervals_api_key: str = Form(""),
):
    _check_csrf(request, csrf)
    prefs = user.settings
    if prefs is None:
        prefs = UserSettings(user_id=user.id)
        db.add(prefs)
    prefs.sync_enabled = 1 if sync_enabled else 0
    prefs.require_gps = 1 if require_gps else 0
    prefs.min_duration_seconds = max(0, min_duration_seconds)
    chosen = [sport for sport in SPORT_CHOICES if sport in sports]
    prefs.sports = ",".join(chosen) if chosen else "Run"
    key = intervals_api_key.strip()
    if key:
        upsert_connection(db, user.id, "intervals", key)
    return RedirectResponse("/settings?notice=saved", status_code=303)


@app.get("/oauth/livelox")
def livelox_start(request: Request, user: User = Depends(require_user)):
    if not settings.livelox_configured:
        return RedirectResponse("/settings?notice=livelox-missing", status_code=303)
    verifier, challenge = new_pkce()
    state = secrets.token_urlsafe(24)
    request.session["livelox_verifier"] = verifier
    request.session["livelox_state"] = state
    return RedirectResponse(authorize_url(settings, state, challenge), status_code=303)


@app.get("/oauth/livelox/callback")
def livelox_callback(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        return RedirectResponse("/settings?notice=livelox-denied", status_code=303)
    if not code or state != request.session.get("livelox_state"):
        return RedirectResponse("/settings?notice=livelox-state", status_code=303)
    verifier = request.session.pop("livelox_verifier", "")
    request.session.pop("livelox_state", None)
    tokens = exchange_code(settings, code, verifier)
    upsert_connection(db, user.id, "livelox", dump_tokens(tokens))
    return RedirectResponse("/settings?notice=livelox-ok", status_code=303)


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    raise exc
