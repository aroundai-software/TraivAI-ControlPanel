"""Read and change the subscription rows. The customer app cannot do this."""

from __future__ import annotations

import base64
import json
from datetime import date

import httpx

SUPABASE_URL = "https://wpjjbzyjixrevnnydegd.supabase.co"


class AdminError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_COLUMNS = "id,machine_id,label,status,expires_at,suspended,last_seen_at,extended_at,last_posted_at,last_invoice"
_FALLBACK = "id,machine_id,label,status,expires_at,suspended,last_seen_at,extended_at"


class SubscriptionDirectory:
    def __init__(self, url: str, key_getter, client: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self._key_getter = key_getter
        self._client = client or httpx.Client(timeout=8.0)
        self.posts_visible = False

    def list_pcs(self) -> list[dict]:
        rows, self.posts_visible = self._select(_COLUMNS)
        if rows is None:
            rows, self.posts_visible = self._select(_FALLBACK)
        if rows is None:
            raise AdminError("The subscription list could not be read.", 502)
        pcs = [_pc(row) for row in rows if isinstance(row, dict) and row.get("machine_id")]
        pcs.sort(key=lambda item: (item["name"].lower(), item["machine_id"]))
        return pcs

    def renew(self, machine_id: str, ends: str) -> dict:
        machine = _machine(machine_id)
        day = _day(ends)
        row = self._one(machine)
        response = self._send(
            "POST",
            "/rest/v1/install_periods",
            {"install_id": row["id"], "kind": "renewal", "expires_at": day},
        )
        if response.status_code >= 400:
            raise AdminError(_database_message(response), 400)
        return _pc(self._one(machine))

    def stop(self, machine_id: str) -> dict:
        return self._set_stopped(_machine(machine_id), True)

    def resume(self, machine_id: str) -> dict:
        return self._set_stopped(_machine(machine_id), False)

    def _set_stopped(self, machine: str, stopped: bool) -> dict:
        row = self._one(machine)
        response = self._send(
            "PATCH",
            "/rest/v1/installs",
            {"suspended": stopped},
            params={"id": f"eq.{row['id']}"},
        )
        if response.status_code >= 400:
            raise AdminError(_database_message(response), 400)
        updated = response.json()
        body = updated[0] if isinstance(updated, list) and updated else row
        body["suspended"] = stopped
        if not body.get("machine_id"):
            body["machine_id"] = machine
        return _pc(body)

    def _select(self, columns: str) -> tuple[list | None, bool]:
        response = self._send("GET", "/rest/v1/installs", None, params={"select": columns})
        if response.status_code == 400 and "last_posted_at" in columns:
            return None, False
        if response.status_code >= 400:
            raise AdminError(_database_message(response), 502)
        body = response.json()
        if not isinstance(body, list):
            raise AdminError("The subscription list could not be read.", 502)
        return body, "last_posted_at" in columns

    def _one(self, machine: str) -> dict:
        response = self._send(
            "GET",
            "/rest/v1/installs",
            None,
            params={"select": "id,machine_id,label,status,expires_at,suspended,last_seen_at,last_posted_at,last_invoice", "machine_id": f"eq.{machine}"},
        )
        if response.status_code == 400:
            response = self._send(
                "GET",
                "/rest/v1/installs",
                None,
                params={"select": "id,machine_id,label,status,expires_at,suspended,last_seen_at", "machine_id": f"eq.{machine}"},
            )
        if response.status_code >= 400:
            raise AdminError(_database_message(response), 502)
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise AdminError("That computer is not on the list.", 404)
        return rows[0]

    def _send(self, method: str, path: str, body: dict | None, params: dict | None = None) -> httpx.Response:
        key = (self._key_getter() or "").strip()
        if not key:
            raise AdminError("Add the Supabase service role key to open this list.", 503)
        assert_service_role(key)
        headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        response = self._client.request(method, f"{self.url}{path}", headers=headers, params=params, json=body)
        if response.status_code in {401, 403}:
            raise AdminError("That service role key was refused.", 400)
        return response


def assert_service_role(key: str) -> None:
    parts = key.split(".")
    if len(parts) != 3:
        raise AdminError("Paste the service role key from Supabase.", 400)
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (json.JSONDecodeError, ValueError):
        raise AdminError("That key could not be read.", 400) from None
    if not isinstance(payload, dict) or payload.get("role") != "service_role":
        raise AdminError("That key cannot read the subscription list. Use the service role key.", 400)


def _machine(value: str) -> str:
    text = (value or "").strip()
    if len(text) < 8 or len(text) > 80 or any(mark in text for mark in "\"\\,()"):
        raise AdminError("That computer could not be found.", 404)
    return text


def _day(value: str) -> str:
    try:
        return date.fromisoformat((value or "").strip()).isoformat()
    except ValueError:
        raise AdminError("Choose an end date.", 400) from None


def _pc(row: dict) -> dict:
    name = str(row.get("label") or "").strip() or "Unnamed computer"
    return {
        "machine_id": row.get("machine_id") or "",
        "name": name,
        "ends": row.get("expires_at") or "",
        "stopped": bool(row.get("suspended")),
        "status": row.get("status") or "",
        "last_seen": row.get("last_seen_at") or "",
        "last_post": row.get("last_posted_at") or "",
        "last_invoice": row.get("last_invoice") or "",
    }


def _database_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except json.JSONDecodeError:
        return "The subscription list could not be saved."
    if isinstance(body, dict):
        message = body.get("message") or body.get("error_description") or body.get("hint")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return "The subscription list could not be saved."
