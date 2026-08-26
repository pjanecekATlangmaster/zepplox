from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import Settings

AUTHORIZE = "https://api.livelox.com/oauth2/authorize"
TOKEN = "https://api.livelox.com/oauth2/token"
IMPORT_ROUTES = "https://api.livelox.com/importableRoutes"
SCOPE = "routes.import"


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
    response.raise_for_status()
    payload = response.json()
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
    return response.json()


def import_status(access_token: str, route_id: str) -> dict:
    response = httpx.get(
        f"{IMPORT_ROUTES}/{route_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def dump_tokens(payload: dict) -> str:
    keep = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "expires_at": payload.get("expires_at", ""),
    }
    return json.dumps(keep)
