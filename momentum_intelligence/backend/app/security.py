"""Phase 11 hardening: production start-up checks, response headers, request limits and login throttling.

None of this replaces running behind HTTPS (docs/DEPLOYMENT.md); it limits the damage of a misconfiguration.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict

from .config import Settings

MIN_TOKEN_LENGTH = 32
DEFAULT_PASSWORDS = ("change-me", "momentum@", "password", "postgres@")
MAX_BODY_BYTES = 1_000_000

# The dashboard is a same-origin single-page app with no inline scripts (Vite build). Inline *styles* are
# needed by React style attributes and the chart library.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class InsecureConfiguration(RuntimeError):
    pass


def production_problems(s: Settings) -> list[str]:
    """Reasons a production process must not start. Empty outside production."""
    if s.app_env != "production":
        return []
    out = []
    token = s.api_token.get_secret_value() if s.api_token else ""
    if len(token) < MIN_TOKEN_LENGTH:
        out.append(f"MI_API_TOKEN must be set to a random value of at least {MIN_TOKEN_LENGTH} characters")
    url = s.database_url.lower()
    if any(p in url for p in DEFAULT_PASSWORDS):
        out.append("MI_DATABASE_URL uses a default or example password")
    if s.data_mode != "live":
        out.append("MI_DATA_MODE must be 'live' in production (demo data is synthetic)")
    if s.ai_enabled and not s.anthropic_api_key:
        out.append("MI_AI_ENABLED is true but MI_ANTHROPIC_API_KEY is empty")
    return out


def enforce(s: Settings) -> None:
    problems = production_problems(s)
    if problems:
        raise InsecureConfiguration("refusing to start in production: " + "; ".join(problems))


class AuthThrottle:
    """Per-client limit on *distinct* wrong bearer tokens (in memory, per process).

    A guessing attack presents many different tokens; a dashboard with one stale saved token repeats the same
    one on every request of a page load, and a visitor without a token sends none. Only the first is counted,
    so neither of the others can lock the owner out.
    """

    def __init__(self, max_failures: int = 10, window_s: float = 300.0):
        self.max_failures, self.window_s = max_failures, window_s
        self._fails: dict[str, dict[str, float]] = defaultdict(dict)   # client -> {token digest: last seen}
        self._lock = threading.Lock()

    def _trim(self, seen: dict, now: float) -> None:
        for k in [k for k, t in seen.items() if now - t > self.window_s]:
            del seen[k]

    def blocked(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            seen = self._fails.get(client)
            if not seen:
                return False
            self._trim(seen, now)
            return len(seen) >= self.max_failures

    def failure(self, client: str, presented: str) -> None:
        digest = hashlib.sha256(presented.encode()).hexdigest()[:16]   # never keep the guessed token itself
        now = time.monotonic()
        with self._lock:
            seen = self._fails[client]
            self._trim(seen, now)
            seen[digest] = now

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


throttle = AuthThrottle()


def install(app) -> None:
    """Security headers, no caching of API responses, and a request-body size limit."""
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _harden(request, call_next):
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BODY_BYTES):
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        if request.method in ("POST", "PUT", "PATCH") and length is None and "chunked" in request.headers.get("transfer-encoding", ""):
            return JSONResponse({"detail": "chunked request bodies are not accepted"}, status_code=411)
        response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response
