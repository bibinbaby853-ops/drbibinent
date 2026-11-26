"""HTTP client for public data sources.

* per-host minimum interval between requests (rate limit)
* retries with exponential backoff and jitter on connection errors, 429 and 5xx
* honours ``Retry-After`` on 429/503
* 404 is not retried: it means "not published" and raises ``DataNotPublished``
* every call is counted so tests can assert behaviour
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .base import DataNotPublished, ProviderError

RETRY_STATUS = {429, 500, 502, 503, 504}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) momentum-intelligence/0.1 (paper-trading research)"


@dataclass
class HttpStats:
    requests: int = 0
    retries: int = 0
    not_found: int = 0


class HttpClient:
    def __init__(self, *, min_interval_s: float = 0.4, max_retries: int = 4, timeout_s: float = 30.0,
                 backoff_base_s: float = 1.0, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, clock=time.monotonic):
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._clock = clock
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.stats = HttpStats()
        self._client = httpx.Client(timeout=timeout_s, follow_redirects=True, transport=transport,
                                    headers={"User-Agent": USER_AGENT, "Accept": "*/*"})

    def _throttle(self, host: str) -> None:
        with self._lock:
            last = self._last.get(host)
            now = self._clock()
            if last is not None and now - last < self.min_interval_s:
                self._sleep(self.min_interval_s - (now - last))
            self._last[host] = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return self.backoff_base_s * (2 ** attempt) + random.uniform(0, self.backoff_base_s / 2)

    def get(self, url: str) -> bytes:
        host = urlparse(url).netloc
        last_error: str = ""
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            self.stats.requests += 1
            try:
                resp = self._client.get(url)
            except httpx.TransportError as exc:
                last_error = f"{exc.__class__.__name__}: {exc}"
                retry_after = None
            else:
                if resp.status_code == 200:
                    return resp.content
                if resp.status_code == 404:
                    self.stats.not_found += 1
                    raise DataNotPublished(f"404 {url}")
                if resp.status_code not in RETRY_STATUS:
                    raise ProviderError(f"HTTP {resp.status_code} for {url}")
                last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
            if attempt < self.max_retries:
                self.stats.retries += 1
                self._sleep(self._backoff(attempt, retry_after))
        raise ProviderError(f"giving up on {url} after {self.max_retries + 1} attempts ({last_error})")

    def close(self) -> None:
        self._client.close()
