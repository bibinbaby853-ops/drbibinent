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


def test_backtest_endpoints_report_missing_runs():
    from fastapi.testclient import TestClient
    from app.main import create_app
    c = TestClient(create_app())
    assert c.get("/api/v1/backtests/999999").status_code == 404
    r = c.get("/api/v1/backtests")
    assert r.status_code == 200 and "Past results" in r.json()["note"]


def test_health_flags_stale_data_with_one_holiday_of_tolerance(monkeypatch):
    from datetime import date, datetime
    from app.api import system
    from app.market_data import calendar
    from app.market_data.calendar import IST
    monkeypatch.setattr(calendar, "now_ist", lambda: datetime(2026, 11, 27, 20, 0, tzinfo=IST))   # Friday evening
    f = lambda d: {k: v for k, v in system._freshness(d).items() if k != "benchmarks"}   # noqa: E731
    assert f(date(2026, 11, 26)) == {"stale": False, "missing_weekdays": 1}       # e.g. a holiday
    assert f(date(2026, 11, 25)) == {"stale": False, "missing_weekdays": 2}
    assert f(date(2026, 11, 23)) == {"stale": True, "missing_weekdays": 4}
    monkeypatch.setattr(calendar, "now_ist", lambda: datetime(2026, 11, 30, 9, 0, tzinfo=IST))    # Monday morning
    assert system._freshness(date(2026, 11, 27))["missing_weekdays"] == 0                         # weekend is not missing
    assert system._freshness(None)["stale"] is True


def test_health_degrades_on_a_recent_job_failure_without_exposing_the_error():
    from sqlalchemy import text
    from app.database import get_engine
    with get_engine().begin() as c:
        c.execute(text("insert into job_runs (job, key, status, started_at, details, error) "
                       "values ('eod', 'health-test', 'failed', now(), '{}', 'secret detail')"))
    try:
        body = client.get("/api/health").json()
        assert body["status"] == "degraded" and body["jobs"]["failures_24h"] >= 1
        assert "secret detail" not in str(body)
    finally:
        with get_engine().begin() as c:
            c.execute(text("delete from job_runs where key = 'health-test'"))


def test_health_names_a_benchmark_carried_forward_in_live_mode():
    from datetime import date
    from app.api import system
    d = date(2026, 11, 25)
    base = {"official_last": {"NIFTY 50": date(2026, 11, 23), "NIFTY 500": d, "INDIA VIX": d},
            "any_source_last": {}, "regime_session": d}
    used = {"NIFTY 50": "carried", "NIFTY 500": "nse", "INDIA VIX": "yahoo"}
    assert system._benchmark_problems({**base, "regime_used": used}, d) == [
        "NIFTY 50: no close for 2026-11-25; the previous close was carried forward"]
    # the official close arrived after scoring: say how to fix it
    late = {**base, "official_last": {**base["official_last"], "NIFTY 50": d}, "regime_used": used}
    assert "rescore with `python -m app.scoring.cli run --start 2026-11-25`" in system._benchmark_problems(late, d)[0]
    # all official, or Yahoo fallback only: no problem
    assert system._benchmark_problems({**base, "regime_used": {"NIFTY 50": "nse", "NIFTY 500": "yahoo"}}, d) == []
    # regime rows from before ADR-018: fall back to stored dates
    old = {"any_source_last": {"NIFTY 50": date(2026, 11, 23), "NIFTY 500": d}, "regime_used": None, "regime_session": d}
    assert system._benchmark_problems(old, d) == ["NIFTY 50: no close stored for 2026-11-25; the previous close is carried forward"]


def test_health_reports_benchmark_problems_in_live_mode_only(monkeypatch):
    from datetime import date
    from app.api import system
    from app.config import get_settings
    monkeypatch.setattr(system, "_latest_session", lambda: date(2026, 11, 25))
    monkeypatch.setattr(system, "_benchmarks", lambda: {"regime_used": {"NIFTY 50": "carried"}, "regime_session": date(2026, 11, 25),
                                                        "official_last": {}})
    assert not any("NIFTY 50" in p for p in client.get("/api/health").json()["problems"])   # demo mode
    monkeypatch.setattr(get_settings(), "data_mode", "live")
    body = client.get("/api/health").json()
    assert body["status"] == "degraded" and any(p.startswith("NIFTY 50: no close") for p in body["problems"])
