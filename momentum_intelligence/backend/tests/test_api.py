from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_paper_mode_and_never_crashes():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["execution_mode"] == "paper"
    assert body["status"] in {"ok", "degraded"}
    assert "reachable" in body["database"]
    assert body["time_ist"].endswith("+05:30")


def test_settings_endpoint_returns_strategy_without_secrets(monkeypatch):
    monkeypatch.setenv("MI_ANTHROPIC_API_KEY", "sk-should-not-leak")
    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"]["max_positions"] == 5
    assert body["strategy"]["execution_mode"] == "paper"
    assert body["derived"]["risk_budget_per_position_inr"] == 500.0
    assert "sk-should-not-leak" not in r.text


def test_openapi_is_published():
    assert client.get("/openapi.json").status_code == 200


def test_data_endpoints_require_the_bearer_token_when_configured(monkeypatch):
    from pydantic import SecretStr
    from fastapi.testclient import TestClient
    from app.config import get_settings
    from app.main import create_app
    monkeypatch.setattr(get_settings(), "api_token", SecretStr("s3cret"))
    c = TestClient(create_app())
    assert c.get("/api/v1/universe").status_code == 401
    assert c.get("/api/v1/universe", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/v1/universe", headers={"Authorization": "Bearer s3cret"}).status_code in (200, 404)
    assert c.get("/api/health").status_code == 200  # health stays open for monitoring


def test_universe_and_indicator_endpoints_report_missing_data_honestly():
    from fastapi.testclient import TestClient
    from app.main import create_app
    c = TestClient(create_app())
    r = c.get("/api/v1/stocks/NOSUCHSTOCK/indicators")
    assert r.status_code == 404 and "unknown symbol" in r.json()["detail"]
