"""Shared API dependencies.

Authentication (ADR-010): when ``MI_API_TOKEN`` is set, data endpoints require
``Authorization: Bearer <token>``. Without a token the API is open, which is
only acceptable on localhost during development; production start-up will
refuse to run without one (Phase 11).
"""

from __future__ import annotations

import hmac
from typing import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import _factory


def require_token(authorization: str | None = Header(default=None)) -> None:
    token = get_settings().api_token
    if token is None:
        return
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid bearer token",
                            headers={"WWW-Authenticate": "Bearer"})


def db_session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
    finally:
        s.close()


Authed = Depends(require_token)
