from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.accounts import (
    consume_otp,
    create_otp,
    delete_user_account,
    ensure_user_settings,
    get_or_create_user,
    normalize_email,
    recent_otp_count,
    utcnow,
)
from app.config import get_settings
from app.db import get_db, init_db
from app.i18n import COOKIE as LANG_COOKIE
from app.i18n import LANGS, resolve_lang, strings_for
from app.mail import send_otp_email
from app.models import User

log = logging.getLogger("zepplox")

ROOT = Path(__file__).resolve().parent
GITHUB_URL = "https://github.com/pjanecekATlangmaster/zepplox"
settings = get_settings()
templates = Jinja2Templates(directory=str(ROOT / "templates"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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


def request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return ""


def _ctx(request: Request, **extra):
    lang = extra.pop("lang", None) or resolve_lang(request)
    return {
        "request": request,
        "app_name": settings.app_name,
        "github_url": GITHUB_URL,
        "csrf": _csrf(request),
        "lang": lang,
        "t": strings_for(lang),
        **extra,
    }


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


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    user: User | None = Depends(current_user),
):
    if user is None:
        return _html(request, "landing.html")
    return _html(request, "home.html", user=user)


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
    try:
        send_otp_email(settings, normalized, code)
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


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(require_user),
):
    return _html(
        request,
        "settings.html",
        user=user,
        sync_enabled=user.settings is None or bool(user.settings.sync_enabled),
        notice=request.query_params.get("notice"),
        error=None,
    )


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
        return _html(
            request,
            "settings.html",
            status_code=400,
            user=user,
            sync_enabled=user.settings is None or bool(user.settings.sync_enabled),
            notice=None,
            error=t["delete_need_confirm"],
        )
    delete_user_account(db, user)
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return _html(request, "privacy.html")


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    if exc.status_code == 303 and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=303)
    raise exc
