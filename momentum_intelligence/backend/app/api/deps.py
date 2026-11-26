"""Shared API dependencies.

Authentication (ADR-010): when ``MI_API_TOKEN`` is set, data endpoints require
``Authorization: Bearer <token>``. Without a token the API is open, which is
only acceptable on localhost during development; production start-up refuses
to run without one (``app.security.enforce``). Repeated failures from one client
with different wrong tokens are throttled (HTTP 429).
"""

from __future__ import annotations

import hmac
from typing import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import _factory
from ..security import throttle


def require_token(request: Request, authorization: str | None = Header(default=None)) -> None:
    token = get_settings().api_token
    if token is None:
        return
    client = request.client.host if request.client else "unknown"
    if throttle.blocked(client):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed attempts; try again later",
                            headers={"Retry-After": str(int(throttle.window_s))})
    expected = f"Bearer {token.get_secret_value()}"
    if authorization is None or not hmac.compare_digest(authorization.encode(), expected.encode()):
        if authorization:
            throttle.failure(client, authorization)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid bearer token",
                            headers={"WWW-Authenticate": "Bearer"})


def db_session() -> Iterator[Session]:
    s = _factory()()
    try:
        yield s
    finally:
        s.close()


Authed = Depends(require_token)
