import base64
import json
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from access import load_config
from directory import AdminError, SubscriptionDirectory, assert_service_role
from server import create_app


def _key(role: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"


def test_a_customer_key_cannot_open_the_list():
    try:
        assert_service_role(_key("anon"))
    except AdminError as exc:
        assert exc.message == "That key cannot read the subscription list. Use the service role key."
    else:
        raise AssertionError("anon key was accepted")


def test_the_list_still_loads_before_last_post_exists():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"message": "column last_posted_at does not exist"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": "1",
                    "machine_id": "pc-1111-2222",
                    "label": "FRONT-DESK",
                    "status": "paid",
                    "expires_at": "2026-12-31",
                    "suspended": False,
                    "last_seen_at": "2026-11-07T06:00:00Z",
                }
            ],
        )

    directory = SubscriptionDirectory(
        "https://example.test",
        lambda: _key("service_role"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    rows = directory.list_pcs()
    assert rows[0]["name"] == "FRONT-DESK"
    assert rows[0]["last_invoice"] == ""
    assert directory.posts_visible is False


def test_renew_stop_and_resume_use_the_existing_rows():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path, json.loads(request.content.decode() or "null")))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "abc",
                        "machine_id": "pc-1111-2222",
                        "label": "FRONT-DESK",
                        "status": "paid",
                        "expires_at": "2026-12-31",
                        "suspended": False,
                    }
                ],
            )
        if request.url.path.endswith("install_periods"):
            return httpx.Response(400, json={"message": "The new end date must be later than the current end"})
        return httpx.Response(200, json=[{"id": "abc", "machine_id": "pc-1111-2222", "label": "FRONT-DESK", "suspended": True}])

    directory = SubscriptionDirectory(
        "https://example.test",
        lambda: _key("service_role"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        directory.renew("pc-1111-2222", "2027-06-01")
    except AdminError as exc:
        assert exc.message == "The new end date must be later than the current end"
    else:
        raise AssertionError("the database refusal was ignored")
    shortened = directory.renew("pc-1111-2222", "2026-12-01")
    assert shortened["ends"] == "2026-12-01"
    assert sent[-1][0] == "PATCH"
    assert sent[-1][2] == {"expires_at": "2026-12-01"}
    stopped = directory.stop("pc-1111-2222")
    assert stopped["stopped"] is True
    assert sent[1][0] == "POST"
    assert sent[1][2]["install_id"] == "abc"
    assert sent[-1][0] == "PATCH"
    assert sent[-1][2] == {"suspended": True}


def test_logs_are_read_for_one_computer_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = str(request.url.query)
        if "install_logs" not in request.url.path:
            return httpx.Response(404, json={"message": "no"})
        return httpx.Response(
            200,
            json=[
                {
                    "logged_at": "2026-11-07T10:00:00Z",
                    "level": "error",
                    "event": "tally_post",
                    "message": "Tally could not be reached",
                    "detail": "Tally could not be reached. 127.0.0.1:9000 refused",
                }
            ],
        )

    directory = SubscriptionDirectory(
        "https://example.test",
        lambda: _key("service_role"),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    rows = directory.logs("pc-1111-2222")
    assert "machine_id=eq.pc-1111-2222" in seen["query"]
    assert "order=logged_at.desc" in seen["query"]
    assert rows[0]["area"] == "Tally"
    assert rows[0]["level"] == "error"
    assert "9000" in rows[0]["detail"]

    missing = SubscriptionDirectory(
        "https://example.test",
        lambda: _key("service_role"),
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404, json={"message": "Could not find the table public.install_logs in the schema cache"}))),
    )
    try:
        missing.logs("pc-1111-2222")
    except AdminError as exc:
        assert exc.message == "Logs are not available yet."
    else:
        raise AssertionError("a missing log table was treated as success")


def test_a_hosted_link_accepts_another_computer(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SUBSCRIPTION_PASSWORD", "page-secret")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "")
    from access import open_config

    config = open_config()
    assert config.hosted is True
    directory = SubscriptionDirectory("https://example.test", lambda: "", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    client = TestClient(create_app(config, directory), client=("10.1.2.3", 50000))
    home = client.get("/")
    assert home.status_code == 200
    refused = client.post("/api/login", json={"password": "nope"})
    assert refused.status_code == 401
    signed = client.post("/api/login", json={"password": "page-secret"})
    assert signed.status_code == 200
    waiting = client.get("/api/pcs")
    assert waiting.status_code == 503


def test_the_page_hides_the_list_until_sign_in(tmp_path):
    config = load_config(tmp_path / "subscription_admin.json")
    password = config.reveal
    directory = SubscriptionDirectory("https://example.test", lambda: "", httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    client = TestClient(create_app(config, directory))
    refused = client.get("/api/pcs")
    assert refused.status_code == 401
    logs_page = client.get("/computers/pc-1111-2222/logs")
    assert logs_page.status_code == 200
    assert 'aria-label="Back"' in logs_page.text
    wrong = client.post("/api/login", json={"password": "nope"})
    assert wrong.status_code == 401
    signed = client.post("/api/login", json={"password": password})
    assert signed.status_code == 200
    waiting = client.get("/api/pcs")
    assert waiting.status_code == 503
    assert "service role key" in waiting.json()["message"]
