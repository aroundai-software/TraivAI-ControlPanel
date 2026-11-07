"""Subscription page. On this computer it stays local. On Vercel the link is public and the secrets stay in Vercel."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from access import AdminConfig, open_config
from directory import SUPABASE_URL, AdminError, SubscriptionDirectory, assert_service_role


PAGE = Path(__file__).with_name("page.html")
_LOCAL = {"127.0.0.1", "::1", "testclient"}


def create_app(config: AdminConfig, directory: SubscriptionDirectory) -> FastAPI:
    app = FastAPI(title="Traiv AI subscriptions", docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.directory = directory

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if config.hosted:
            return await call_next(request)
        host = request.client.host if request.client else ""
        if host not in _LOCAL:
            return JSONResponse(status_code=403, content={"message": "Open this page on this computer."})
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/computers/{machine_id}/logs", response_class=HTMLResponse)
    def computer_logs(machine_id: str) -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/session")
    def session(request: Request) -> dict:
        return {
            "signed_in": _signed_in(request),
            "configured": bool(config.service_role_key.strip()),
            "hosted": config.hosted,
            "first_password": "" if _signed_in(request) else config.reveal,
            "posts_visible": directory.posts_visible,
        }

    @app.post("/api/login")
    def login(body: dict = Body(...)) -> JSONResponse:
        password = str(body.get("password") or "")
        if not config.check_password(password):
            return JSONResponse(status_code=401, content={"message": "That password is wrong."})
        config.clear_reveal()
        response = JSONResponse({"ok": True, "configured": bool(config.service_role_key.strip())})
        response.set_cookie("session", _session_token(config), httponly=True, samesite="lax", path="/")
        return response

    @app.post("/api/key")
    def save_key(request: Request, body: dict = Body(...)) -> JSONResponse:
        _require(request)
        if config.hosted:
            return JSONResponse(status_code=400, content={"message": "Set the service role key in the Vercel project settings."})
        key = str(body.get("key") or "").strip()
        try:
            assert_service_role(key)
        except AdminError as exc:
            return JSONResponse(status_code=exc.status, content={"message": exc.message})
        config.save_key(key)
        return JSONResponse({"ok": True})

    @app.get("/api/pcs")
    def pcs(request: Request) -> JSONResponse:
        _require(request)
        try:
            rows = directory.list_pcs()
        except AdminError as exc:
            return JSONResponse(status_code=exc.status, content={"message": exc.message})
        return JSONResponse({"pcs": rows, "posts_visible": directory.posts_visible})

    @app.post("/api/pcs/{machine_id}/renew")
    def renew(machine_id: str, request: Request, body: dict = Body(...)) -> JSONResponse:
        return _change(request, lambda: directory.renew(machine_id, str(body.get("ends") or "")))

    @app.post("/api/pcs/{machine_id}/stop")
    def stop(machine_id: str, request: Request) -> JSONResponse:
        return _change(request, lambda: directory.stop(machine_id))

    @app.post("/api/pcs/{machine_id}/resume")
    def resume(machine_id: str, request: Request) -> JSONResponse:
        return _change(request, lambda: directory.resume(machine_id))

    @app.get("/api/pcs/{machine_id}/logs")
    def logs(machine_id: str, request: Request) -> JSONResponse:
        _require(request)
        try:
            rows = directory.logs(machine_id)
        except AdminError as exc:
            return JSONResponse(status_code=exc.status, content={"message": exc.message})
        return JSONResponse({"logs": rows})

    def _require(request: Request) -> None:
        if not _signed_in(request):
            raise _Auth()

    def _signed_in(request: Request) -> bool:
        cookie = request.cookies.get("session") or ""
        return bool(cookie) and hmac.compare_digest(cookie, _session_token(config))

    def _change(request: Request, action) -> JSONResponse:
        if not _signed_in(request):
            return JSONResponse(status_code=401, content={"message": "Sign in again."})
        try:
            row = action()
        except AdminError as exc:
            return JSONResponse(status_code=exc.status, content={"message": exc.message})
        return JSONResponse({"ok": True, "pc": row, "posts_visible": directory.posts_visible})

    @app.exception_handler(_Auth)
    async def unauthorized(_request: Request, _exc: _Auth) -> JSONResponse:
        return JSONResponse(status_code=401, content={"message": "Sign in again."})

    return app


class _Auth(Exception):
    pass


def _session_token(config: AdminConfig) -> str:
    return hmac.new(config.password_hash.encode(), b"traiv-subscription-v1", hashlib.sha256).hexdigest()


def main() -> None:
    import uvicorn

    config = open_config()
    directory = SubscriptionDirectory(SUPABASE_URL, lambda: config.service_role_key)
    uvicorn.run(create_app(config, directory), host="127.0.0.1", port=8770, log_level="info")


if __name__ == "__main__":
    main()
