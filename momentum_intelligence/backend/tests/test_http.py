import httpx
import pytest

from app.market_data.base import DataNotPublished, ProviderError
from app.market_data.http import HttpClient


def client(handler, **kw):
    sleeps = []
    c = HttpClient(transport=httpx.MockTransport(handler), sleep=sleeps.append, backoff_base_s=1.0, **kw)
    return c, sleeps


def test_retries_429_and_honours_retry_after():
    seq = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, content=b"ok")])
    c, sleeps = client(lambda req: next(seq), min_interval_s=0)
    assert c.get("https://x.test/a") == b"ok"
    assert c.stats.requests == 2 and c.stats.retries == 1
    assert 7.0 in sleeps


def test_retries_5xx_with_exponential_backoff_then_gives_up():
    c, sleeps = client(lambda req: httpx.Response(503), min_interval_s=0, max_retries=3)
    with pytest.raises(ProviderError, match="after 4 attempts"):
        c.get("https://x.test/a")
    assert c.stats.requests == 4
    backoffs = [s for s in sleeps if s >= 1.0]
    assert len(backoffs) == 3 and backoffs[0] < backoffs[1] < backoffs[2]


def test_connection_errors_are_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=b"x")

    c, _ = client(handler, min_interval_s=0)
    assert c.get("https://x.test/a") == b"x" and calls["n"] == 3


def test_404_means_not_published_and_is_not_retried():
    c, _ = client(lambda req: httpx.Response(404), min_interval_s=0)
    with pytest.raises(DataNotPublished):
        c.get("https://x.test/missing")
    assert c.stats.requests == 1


def test_other_client_errors_fail_fast():
    c, _ = client(lambda req: httpx.Response(403), min_interval_s=0)
    with pytest.raises(ProviderError, match="403"):
        c.get("https://x.test/a")
    assert c.stats.requests == 1


def test_rate_limit_spaces_requests_per_host():
    t = {"now": 100.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        t["now"] += s

    c = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"")), sleep=sleep,
                   clock=lambda: t["now"], min_interval_s=0.5)
    c.get("https://a.test/1")
    c.get("https://a.test/2")
    c.get("https://b.test/1")  # another host is not throttled by a.test
    assert sleeps == [pytest.approx(0.5)]
