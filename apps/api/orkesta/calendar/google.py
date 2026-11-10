"""Google Calendar API (calendario principal de la persona). Mismo permiso OAuth que Gmail."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from orkesta.research import fetch

API = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events"


class CalendarError(Exception):
    def __init__(self, message: str, *, gone: bool = False, auth: bool = False):
        super().__init__(message)
        self.gone, self.auth = gone, auth


def _client() -> httpx.Client:
    return httpx.Client(timeout=30.0, transport=fetch.TRANSPORT)


def _explain(response: httpx.Response) -> CalendarError:
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    text = str(error).lower()
    if response.status_code == 410:
        return CalendarError("La sincronización expiró; se vuelve a cargar.", gone=True)
    if "accessnotconfigured" in text or "service_disabled" in text or "has not been used" in text:
        return CalendarError(
            "La API de Google Calendar no está habilitada en Google Cloud: APIs y servicios → "
            "Biblioteca → Google Calendar API → Habilitar."
        )
    if response.status_code in (401, 403) and (
        "insufficient" in text or "scope" in text or response.status_code == 401
    ):
        return CalendarError(
            "Falta el permiso de calendario: reconecta Gmail aceptando el calendario.", auth=True
        )
    detail = str(error.get("message", ""))[:160] if isinstance(error, dict) else ""
    return CalendarError(f"Google Calendar no respondió ({response.status_code}). {detail}".strip())


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def list_changes(token: str, sync_token: str | None) -> tuple[list[dict[str, Any]], str | None]:
    """Eventos nuevos o cambiados desde la última vez (todos los del último mes y los próximos
    6 meses la primera vez). Devuelve los eventos y el token para la próxima vuelta."""
    params: dict[str, Any] = {"maxResults": 250, "singleEvents": "true", "showDeleted": "true"}
    if sync_token:
        params["syncToken"] = sync_token
    else:
        now = datetime.now(UTC)
        params["timeMin"] = (now - timedelta(days=30)).isoformat()
        params["timeMax"] = (now + timedelta(days=183)).isoformat()
    items: list[dict[str, Any]] = []
    with _client() as client:
        for _page in range(20):
            response = client.get(API, params=params, headers=_headers(token))
            if response.status_code != 200:
                raise _explain(response)
            data = response.json()
            items.extend(data.get("items") or [])
            if data.get("nextPageToken"):
                params["pageToken"] = data["nextPageToken"]
                continue
            return items, data.get("nextSyncToken")
    return items, None


def _when(at: datetime, all_day: bool) -> dict[str, str]:
    if all_day:
        return {"date": at.date().isoformat()}
    return {"dateTime": at.astimezone(UTC).isoformat()}


def body(
    *,
    title: str,
    description: str,
    location: str,
    start_at: datetime,
    end_at: datetime,
    all_day: bool,
    attendees: list[dict[str, Any]],
    meet: bool,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "summary": title,
        "description": description,
        "location": location,
        "start": _when(start_at, all_day),
        "end": _when(end_at if not all_day else max(end_at, start_at + timedelta(days=1)), all_day),
        "attendees": [
            {"email": a["email"], **({"displayName": a["name"]} if a.get("name") else {})}
            for a in attendees
            if a.get("email")
        ],
    }
    if meet:
        data["conferenceData"] = {
            "createRequest": {
                "requestId": f"orkesta-{int(datetime.now(UTC).timestamp() * 1000)}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
    return data


def insert(token: str, data: dict[str, Any], notify: bool) -> dict[str, Any]:
    params = {"conferenceDataVersion": 1, "sendUpdates": "all" if notify else "none"}
    with _client() as client:
        response = client.post(API, params=params, json=data, headers=_headers(token))
    if response.status_code not in (200, 201):
        raise _explain(response)
    result: dict[str, Any] = response.json()
    return result


def patch(token: str, google_id: str, data: dict[str, Any], notify: bool) -> dict[str, Any]:
    params = {"conferenceDataVersion": 1, "sendUpdates": "all" if notify else "none"}
    with _client() as client:
        response = client.patch(
            f"{API}/{quote(google_id, safe='')}", params=params, json=data, headers=_headers(token)
        )
    if response.status_code != 200:
        raise _explain(response)
    result: dict[str, Any] = response.json()
    return result


def delete(token: str, google_id: str, notify: bool) -> None:
    with _client() as client:
        response = client.delete(
            f"{API}/{quote(google_id, safe='')}",
            params={"sendUpdates": "all" if notify else "none"},
            headers=_headers(token),
        )
    if response.status_code not in (200, 204, 404, 410):
        raise _explain(response)


def parse_time(value: dict[str, Any] | None) -> tuple[datetime | None, bool]:
    """{dateTime} o {date} de Google → (momento, todo el día)."""
    value = value or {}
    if value.get("dateTime"):
        return datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00")), False
    if value.get("date"):
        d = date.fromisoformat(str(value["date"]))
        return datetime(d.year, d.month, d.day, tzinfo=UTC), True
    return None, False
