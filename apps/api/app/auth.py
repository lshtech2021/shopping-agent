"""Shared extension-token checks for REST and action broker routes."""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from app.config import settings


def tokens_match(provided: str | None, expected: str | None) -> bool:
    return hmac.compare_digest(provided or "", expected or "")


def extension_auth_enabled() -> bool:
    return bool(settings.auth_required or settings.extension_token)


def require_extension_token(token: str | None) -> None:
    """Fail closed when auth is required or a token is configured."""
    expected = settings.extension_token or ""
    if not extension_auth_enabled():
        return
    if not expected or not tokens_match(token, expected):
        raise HTTPException(status_code=403, detail="extension token required")


def require_extension_token_header(
    x_extension_token: str | None = Header(default=None),
) -> None:
    require_extension_token(x_extension_token)
