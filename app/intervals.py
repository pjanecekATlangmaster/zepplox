from __future__ import annotations

from datetime import date

import httpx

BASE = "https://intervals.icu/api/v1"
PREVIEW_DAYS = 30


class IntervalsAuthError(Exception):
    """API key was rejected (401/403)."""


def _client(api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        auth=("API_KEY", api_key.strip()),
        timeout=45.0,
        headers={"Accept": "application/json"},
        follow_redirects=True,
    )


def _check(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise IntervalsAuthError("Intervals.icu rejected the API key.")
    response.raise_for_status()


def get_athlete(api_key: str) -> dict:
    with _client(api_key) as client:
        response = client.get("/athlete/0")
        _check(response)
        data = response.json()
        if isinstance(data, dict) and "id" not in data and isinstance(data.get("athlete"), dict):
            data = data["athlete"]
        return data if isinstance(data, dict) else {}


def athlete_display_name(athlete: dict) -> str:
    name = str(athlete.get("name") or "").strip()
    if name:
        return name[:200]
    parts = [str(athlete.get("firstname") or "").strip(), str(athlete.get("lastname") or "").strip()]
    joined = " ".join(part for part in parts if part)
    return (joined or str(athlete.get("id") or "Intervals.icu"))[:200]


def list_activities(api_key: str, oldest: date, newest: date) -> list[dict]:
    with _client(api_key) as client:
        response = client.get(
            "/athlete/0/activities",
            params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
        )
        _check(response)
        data = response.json()
        return data if isinstance(data, list) else []


def download_fit(api_key: str, activity_id: str) -> bytes:
    """Download a Livelox-safe track file.

    Zepp/Amazfit originals from Intervals ``/file`` are FIT but start with
    developer_data_id (global message 207). Livelox requires file_id (0) first
    and rejects them with InvalidRouteFileFormatException. Intervals
    ``/fit-file`` rewrites the same GPS into a spec-compliant FIT.
    """
    headers = {"Accept": "application/octet-stream"}
    with _client(api_key) as client:
        response = client.get(f"/activity/{activity_id}/fit-file", headers=headers)
        if response.status_code == 404:
            response = client.get(f"/activity/{activity_id}/file", headers=headers)
        response.raise_for_status()
        return response.content


SPORT_ALIASES = {
    "run": "Run",
    "running": "Run",
    "trailrun": "TrailRun",
    "trail run": "TrailRun",
    "trail running": "TrailRun",
    "walk": "Walk",
    "walking": "Walk",
    "hike": "Hike",
    "hiking": "Hike",
    "ride": "Ride",
    "cycling": "Ride",
    "bike": "Ride",
    "mountainbikeride": "MountainBikeRide",
    "mountain bike": "MountainBikeRide",
}


def canonical_sport(raw: str) -> str:
    value = str(raw or "").strip()
    return SPORT_ALIASES.get(value.lower()) or value


def _stream_types(activity: dict) -> list[str]:
    raw = activity.get("stream_types")
    if isinstance(raw, str):
        return [part.strip().lower() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip().lower() for part in raw if part]
    return []


def _distance_m(activity: dict) -> float:
    for key in ("distance", "icu_distance"):
        raw = activity.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def _is_trainer(activity: dict) -> bool:
    value = activity.get("trainer")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def activity_has_gps(activity: dict) -> bool:
    """Intervals.icu often leaves has_map / start_latlng empty on list payloads."""
    if activity.get("has_map") is True:
        return True
    start = activity.get("icu_start_latlng") or activity.get("start_latlng")
    if start:
        return True
    if "latlng" in _stream_types(activity):
        return True
    file_type = str(activity.get("file_type") or "").lower().lstrip(".")
    if file_type in {"gpx", "tcx"}:
        return True
    if _is_trainer(activity):
        return False
    # Zepp/Amazfit files are FIT; Intervals often omits has_map and latlng on the list.
    return file_type.startswith("fit") and _distance_m(activity) >= 50


def summarize_activity(activity: dict) -> dict[str, object]:
    distance_m = _distance_m(activity)
    duration = int(activity.get("moving_time") or activity.get("elapsed_time") or 0)
    start = str(activity.get("start_date_local") or activity.get("start_date") or "")
    return {
        "id": str(activity.get("id") or ""),
        "name": str(activity.get("name") or activity.get("id") or ""),
        "sport": canonical_sport(str(activity.get("type") or activity.get("sport") or "")),
        "start": start.replace("T", " ")[:16],
        "distance_km": round(distance_m / 1000, 2) if distance_m else 0,
        "duration_min": duration // 60,
        "has_gps": activity_has_gps(activity),
    }
