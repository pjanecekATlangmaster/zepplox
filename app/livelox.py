from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import Settings

AUTHORIZE = "https://api.livelox.com/oauth2/authorize"
TOKEN = "https://api.livelox.com/oauth2/token"
REVOKE = "https://api.livelox.com/oauth2/revoke"
USERINFO = "https://api.livelox.com/oauth2/userinfo"
IMPORT_ROUTES = "https://api.livelox.com/importableRoutes"
SCOPE = "routes.import"


class LiveloxOAuthError(Exception):
    """Authorization code exchange failed."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def authorize_url(settings: Settings, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "scope": SCOPE,
            "redirect_uri": settings.livelox_callback,
            "client_id": settings.livelox_client_id,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE}?{query}"


def exchange_code(settings: Settings, code: str, verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.livelox_client_id,
        "code_verifier": verifier,
        "scope": SCOPE,
        "redirect_uri": settings.livelox_callback,
    }
    response = httpx.post(TOKEN, data=data, timeout=30.0)
    if response.status_code >= 400:
        raise LiveloxOAuthError(f"token exchange HTTP {response.status_code}")
    payload = response.json()
    if not payload.get("access_token"):
        raise LiveloxOAuthError("token response missing access_token")
    return _with_expiry(payload)


def refresh_access_token(settings: Settings, refresh_token: str) -> dict:
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.livelox_client_id,
        "refresh_token": refresh_token,
    }
    response = httpx.post(TOKEN, data=data, timeout=30.0)
    response.raise_for_status()
    payload = response.json()
    if "refresh_token" not in payload:
        payload["refresh_token"] = refresh_token
    return _with_expiry(payload)


def _with_expiry(payload: dict) -> dict:
    expires_in = int(payload.get("expires_in") or 86400)
    payload["expires_at"] = (datetime.now(UTC) + timedelta(seconds=expires_in - 60)).isoformat()
    return payload


def fetch_userinfo_name(access_token: str) -> str:
    try:
        response = httpx.get(
            USERINFO,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    name = str(payload.get("name") or "").strip()
    if not name:
        name = " ".join(
            part
            for part in (payload.get("given_name"), payload.get("family_name"))
            if part
        ).strip()
    if not name:
        name = str(payload.get("email") or payload.get("preferred_username") or "").strip()
    return name[:200]


def tokens_need_refresh(stored: dict) -> bool:
    raw = stored.get("expires_at")
    if not raw:
        return True
    expires = datetime.fromisoformat(raw)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return datetime.now(UTC) >= expires


def import_route(access_token: str, route_id: str, file_bytes: bytes) -> dict:
    payload = {
        "id": route_id[:48],
        "data": base64.b64encode(file_bytes).decode("ascii"),
        "deviceModel": "Amazfit / Zepp",
    }
    response = httpx.post(
        IMPORT_ROUTES,
        json=payload,
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        timeout=60.0,
    )
    response.raise_for_status()
    if not response.content:
        return {"id": route_id}
    posted = response.json()
    if not isinstance(posted, dict):
        return {"id": route_id}
    return posted


def import_status(access_token: str, route_id: str) -> dict:
    response = httpx.get(
        f"{IMPORT_ROUTES}/{route_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )
    if response.status_code == 404:
        return {"status": "pending", "not_found": True}
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"status": "pending"}


def route_id_for(activity_id: str, *, fresh: bool = False) -> str:
    if fresh:
        return f"zepplox-{activity_id}-{secrets.token_hex(3)}"[:48]
    return f"zepplox-{activity_id}"[:48]


def lookup_existing_route(access_token: str, route_ids: list[str]) -> tuple[str, dict] | None:
    seen: set[str] = set()
    for route_id in route_ids:
        rid = str(route_id or "").strip()[:48]
        if not rid or rid in seen:
            continue
        seen.add(rid)
        meta = import_status(access_token, rid)
        if meta.get("not_found"):
            continue
        status = str(meta.get("status") or "")
        if status in {"imported", "pending"}:
            return rid, meta
    return None


def event_label(meta: dict) -> str:
    return " / ".join(
        part
        for part in (meta.get("eventName"), meta.get("className"), meta.get("viewerUrl"))
        if part
    )


def poll_import(access_token: str, route_id: str, rounds: int = 8) -> dict:
    last: dict = {"status": "pending", "not_found": True}
    for _ in range(rounds):
        time.sleep(2)
        last = import_status(access_token, route_id)
        status = str(last.get("status") or "pending")
        if status == "imported":
            return last
        if status == "error":
            raise RuntimeError(str(last.get("errorMessage") or "Livelox import error"))
    return last


def livelox_http_message(exc: BaseException) -> str:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        code = exc.response.status_code
        if code == 404:
            return "Livelox trasu nenašel (404). Po smazání v Liveloxu ji odešlete znovu."
        if code in {401, 403}:
            return "Livelox odmítl přístup. Znovu propojte Livelox v nastavení."
        return f"Livelox HTTP {code}"
    text = str(exc)[:500]
    if "InvalidRouteFileFormatException" in text or "global message number is not file_id" in text:
        return "Livelox nepřijal soubor ze Zeppu (FIT nezačíná file_id). ZeppLox má posílat FIT z Intervals.icu."
    return text


def revoke_token(settings: Settings, token: str) -> None:
    if not token:
        return
    try:
        httpx.post(
            REVOKE,
            data={"token": token, "client_id": settings.livelox_client_id},
            timeout=15.0,
        )
    except httpx.HTTPError:
        return


def revoke_stored(settings: Settings, stored: dict) -> None:
    revoke_token(settings, str(stored.get("refresh_token") or ""))
    revoke_token(settings, str(stored.get("access_token") or ""))


def dump_tokens(payload: dict) -> str:
    keep = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": payload.get("expires_at", ""),
    }
    return json.dumps(keep)
