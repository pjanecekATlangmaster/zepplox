from __future__ import annotations

from datetime import date

import httpx

BASE = "https://intervals.icu/api/v1"


def _client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        auth=("API_KEY", api_key),
        timeout=45.0,
        headers={"Accept": "application/json"},
    )


def list_activities(api_key: str, oldest: date, newest: date) -> list[dict]:
    with _client(api_key) as client:
        response = client.get(
            "/athlete/0/activities",
            params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []


def download_fit(api_key: str, activity_id: str) -> bytes:
    with _client(api_key) as client:
        response = client.get(f"/activity/{activity_id}/file")
        if response.status_code == 404:
            response = client.get(f"/activity/{activity_id}/fit-file")
        response.raise_for_status()
        return response.content


def activity_has_gps(activity: dict) -> bool:
    if activity.get("has_map") is True:
        return True
    start = activity.get("icu_start_latlng") or activity.get("start_latlng")
    return bool(start)
