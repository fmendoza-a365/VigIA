from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any


AUTH_COOKIE_NAME = "vigia_session"


def session_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("VIGIA_SESSION_TTL_SECONDS", "28800"))
    except ValueError:
        configured = 28800
    return max(900, min(configured, 7 * 24 * 60 * 60))


def session_secret() -> bytes:
    configured = os.getenv("VIGIA_SESSION_SECRET", "").strip()
    if not configured:
        configured = "vigia-local-development-secret-change-me"
    return configured.encode("utf-8")


def configured_username() -> str:
    return os.getenv("VIGIA_ADMIN_USER", "admin").strip() or "admin"


def credentials_are_valid(username: str, password: str) -> bool:
    expected_username = configured_username()
    expected_password = os.getenv("VIGIA_ADMIN_PASSWORD", "").strip()
    if not expected_password:
        return False
    return secrets.compare_digest(username.strip(), expected_username) and secrets.compare_digest(
        password,
        expected_password,
    )


def create_session_token(username: str, *, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    payload = {
        "sub": username,
        "iat": issued_at,
        "exp": issued_at + session_ttl_seconds(),
        "nonce": secrets.token_hex(8),
    }
    encoded_payload = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(session_secret(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def verify_session_token(token: str | None, *, now: int | None = None) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    encoded_payload, encoded_signature = token.split(".", 1)
    expected_signature = hmac.new(
        session_secret(),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    try:
        provided_signature = _decode(encoded_signature)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(provided_signature, expected_signature):
        return None

    try:
        payload = json.loads(_decode(encoded_payload).decode("utf-8"))
        expires_at = int(payload["exp"])
        username = str(payload["sub"])
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    current_time = int(time.time() if now is None else now)
    if expires_at <= current_time or username != configured_username():
        return None
    return payload


def cookie_secure() -> bool:
    return os.getenv("VIGIA_COOKIE_SECURE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))
