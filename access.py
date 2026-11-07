"""The subscription page password and service role key stay on this computer."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def config_path() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / "Traiv AI" / "subscription_admin.json"


@dataclass
class AdminConfig:
    path: Path
    password_hash: str
    password_salt: str
    reveal: str
    service_role_key: str
    hosted: bool = False

    def check_password(self, password: str) -> bool:
        return _hash(password, self.password_salt) == self.password_hash

    def clear_reveal(self) -> None:
        if not self.reveal:
            return
        self.reveal = ""
        self.save()

    def save_key(self, key: str) -> None:
        self.service_role_key = key.strip()
        self.save()

    def save(self) -> None:
        if self.hosted:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "password_hash": self.password_hash,
                    "password_salt": self.password_salt,
                    "reveal": self.reveal,
                    "service_role_key": self.service_role_key,
                }
            ),
            encoding="utf-8",
        )


def open_config() -> AdminConfig:
    """Use the Vercel secrets when this page is hosted. Otherwise use the file on this computer."""
    password = os.environ.get("SUBSCRIPTION_PASSWORD", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if password or key or os.environ.get("VERCEL"):
        salt = "traiv-subscription-hosted"
        return AdminConfig(
            path=Path(),
            password_hash=_hash(password, salt) if password else "missing",
            password_salt=salt,
            reveal="",
            service_role_key=key,
            hosted=True,
        )
    return load_config()


def load_config(path: Path | None = None) -> AdminConfig:
    target = path or config_path()
    if target.is_file():
        try:
            saved = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        if isinstance(saved, dict) and saved.get("password_hash") and saved.get("password_salt"):
            return AdminConfig(
                path=target,
                password_hash=str(saved.get("password_hash")),
                password_salt=str(saved.get("password_salt")),
                reveal=str(saved.get("reveal") or ""),
                service_role_key=str(saved.get("service_role_key") or ""),
            )
    password = secrets.token_urlsafe(9)
    salt = secrets.token_hex(16)
    config = AdminConfig(
        path=target,
        password_hash=_hash(password, salt),
        password_salt=salt,
        reveal=password,
        service_role_key="",
    )
    config.save()
    return config


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
