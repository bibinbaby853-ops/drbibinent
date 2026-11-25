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
