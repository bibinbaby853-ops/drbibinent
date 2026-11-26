"""Phase 11: production start-up checks, headers, limits, throttling, and no secrets in source control."""

import pathlib
import re
import subprocess

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import security
from app.config import Settings, get_settings
from app.main import create_app

REPO = pathlib.Path(__file__).resolve().parents[3]
GOOD = dict(app_env="production", api_token=SecretStr("x" * 40), data_mode="live",
            database_url="postgresql+psycopg://mi:Zq8-long-random-pw@db:5432/momentum")


def test_development_has_no_production_requirements():
    assert security.production_problems(Settings(app_env="development")) == []


def test_a_correct_production_configuration_starts():
    assert security.production_problems(Settings(**GOOD)) == []


@pytest.mark.parametrize("change, message", [
    ({"api_token": None}, "MI_API_TOKEN"),
    ({"api_token": SecretStr("short")}, "MI_API_TOKEN"),
    ({"database_url": "postgresql+psycopg://momentum:change-me@db/momentum"}, "default or example password"),
    ({"database_url": "postgresql+psycopg://momentum:momentum@db/momentum"}, "default or example password"),
    ({"data_mode": "demo"}, "MI_DATA_MODE"),
    ({"ai_enabled": True}, "MI_ANTHROPIC_API_KEY"),
])
def test_production_refuses_insecure_settings(change, message):
    s = Settings(**{**GOOD, **change})
    with pytest.raises(security.InsecureConfiguration, match=message):
        security.enforce(s)


def test_app_and_scheduler_refuse_to_start_when_insecure(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "api_token", None)
    with pytest.raises(security.InsecureConfiguration):
        create_app()
    from app.scheduler.service import main
    with pytest.raises(security.InsecureConfiguration):
        main(["once", "pre_market"])


def test_production_hides_interactive_docs(monkeypatch):
    monkeypatch.setattr(security, "DEFAULT_PASSWORDS", ())   # the local test database keeps its dev password
    for k, v in GOOD.items():
        if k != "database_url":
            monkeypatch.setattr(get_settings(), k, v)
    c = TestClient(create_app())
    docs, schema = c.get("/docs"), c.get("/openapi.json")
    # unknown paths fall through to the dashboard page (or 404 without a build), never Swagger or the schema
    assert "swagger" not in docs.text.lower() and '"openapi"' not in schema.text
    assert c.get("/api/health").status_code == 200


def test_security_headers_and_no_store_on_api():
    c = TestClient(create_app())
    r = c.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "script-src 'self'" in r.headers["content-security-policy"] and "unsafe-eval" not in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_oversized_request_bodies_are_rejected():
    c = TestClient(create_app())
    r = c.put("/api/v1/settings", content=b"{" + b" " * (security.MAX_BODY_BYTES + 10) + b"}",
              headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_guessing_many_tokens_is_throttled_but_a_stale_token_or_no_token_is_not(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_token", SecretStr("y" * 40))
    security.throttle.reset()
    c = TestClient(create_app())
    good = {"Authorization": f"Bearer {'y' * 40}"}
    try:
        for _ in range(30):   # a page load without a token, or with one stale saved token, repeated
            assert c.get("/api/v1/universe").status_code == 401
            assert c.get("/api/v1/universe", headers={"Authorization": "Bearer stale"}).status_code == 401
        assert c.get("/api/v1/universe", headers=good).status_code in (200, 404)
        for i in range(security.throttle.max_failures):
            assert c.get("/api/v1/universe", headers={"Authorization": f"Bearer guess{i}"}).status_code in (401, 429)
        # once blocked, even the right token is refused until the window passes (stops brute force)
        r = c.get("/api/v1/universe", headers=good)
        assert r.status_code == 429 and "retry-after" in r.headers
        assert c.get("/api/health").status_code == 200
    finally:
        security.throttle.reset()


def test_throttle_window_expires():
    t = security.AuthThrottle(max_failures=2, window_s=0.05)
    t.failure("a", "x"), t.failure("a", "y")
    assert t.blocked("a") and not t.blocked("b")
    t.failure("c", "same"), t.failure("c", "same")
    assert not t.blocked("c")
    import time
    time.sleep(0.06)
    assert not t.blocked("a")


SECRET_PATTERNS = [
    r"sk-ant-[A-Za-z0-9_-]{20,}",                 # Anthropic keys
    r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY",
    r"ghp_[A-Za-z0-9]{30,}", r"github_pat_[A-Za-z0-9_]{30,}",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)access_token\s*[=:]\s*['\"][A-Za-z0-9]{24,}['\"]",   # Kite-style session tokens
]


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [REPO / f for f in out.splitlines()]


def test_no_env_files_or_secrets_are_tracked_in_git():
    files = tracked_files()
    names = [f.name for f in files]
    assert ".env" not in names, ".env must never be committed"
    hits = []
    for f in files:
        if f.suffix in {".png", ".jpg", ".ico", ".xlsx", ".zip", ".gz", ".dump"} or not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{f.relative_to(REPO)}: {p}" for p in SECRET_PATTERNS if re.search(p, text)]
    assert not hits, hits


def test_frontend_source_holds_no_credentials():
    src = REPO / "momentum_intelligence" / "frontend" / "src"
    text = "\n".join(p.read_text(encoding="utf-8") for p in src.rglob("*.ts*"))
    for word in ("sk-ant-", "api_secret", "x-api-key", "api.anthropic.com", "kite.zerodha.com"):
        assert word not in text, word
    # build-time variables end up in the public bundle; none may carry a secret
    assert not re.search(r"import\.meta\.env\.\w*(KEY|SECRET|TOKEN)", text)
