"""Phase 8 API contract tests: settings (validation, audit, effect), dashboard endpoints on demo data."""

from datetime import date

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client():
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from app.config import invalidate_config_cache
    from app.database import ping, session_scope
    ok, detail = ping()
    if not ok:
        pytest.skip(detail)
    command.upgrade(Config("alembic.ini"), "head")
    from app.main import create_app
    with session_scope() as db:
        db.execute(text("truncate app_settings, signal_events, trade_signals, trade_journal, risk_events, portfolio_snapshots, "
                        "paper_orders, paper_positions, trade_proposals, paper_accounts, momentum_scores, relative_strength, "
                        "sector_data, market_regime, stock_universe_snapshots, technical_indicators, surveillance_flags, market_candles, "
                        "corporate_actions, data_quality_issues, stock_symbol_history, stocks, trading_sessions, data_provider_logs, "
                        "backtest_runs restart identity cascade"))
    invalidate_config_cache()
    c = TestClient(create_app())
    # settings through the API (demo stocks are small: lower the liquidity bar)
    r = c.put("/api/v1/settings", json={"changes": {"min_median_traded_value_cr": 1.0}, "note": "demo data"})
    assert r.status_code == 200 and r.json()["saved"]
    from app.config import default_strategy_config
    cfg = default_strategy_config()
    assert cfg.min_median_traded_value_cr == 1.0
    from app.market_data.demo import DemoProvider
    from app.market_data.indices import ingest_demo_index
    from app.market_data.ingest import ingest_range, refresh_adjustments
    from app.scoring.pipeline import run_range
    from app.strategies.pipeline import build
    from app.universe.engine import build_snapshot
    p = DemoProvider(n_stocks=20, start=date(2024, 6, 3), end=date(2025, 9, 30), seed=5)
    ingest_range(session_scope, p, p.start, p.end)
    with session_scope() as db:
        refresh_adjustments(db, source="demo")
        ingest_demo_index(db, p, p.start, p.end)
    days = p.sessions[-30:]
    for d in days:
        with session_scope() as db:
            build_snapshot(db, d, cfg, source="demo")
    with session_scope() as db:
        run_range(db, days[0], days[-1], cfg, source="demo", detail_sessions=2)
    with session_scope() as db:
        build(db, days[-1], cfg, source="demo")
    yield c, days
    with session_scope() as db:
        db.execute(text("truncate app_settings restart identity"))
    invalidate_config_cache()


def test_settings_validation_audit_and_locks(client):
    c, _ = client
    bad = c.put("/api/v1/settings", json={"changes": {"max_positions": 0}})
    assert bad.status_code == 422 and any("max_positions" in e["field"] for e in bad.json()["detail"])
    incoherent = c.put("/api/v1/settings", json={"changes": {"risk_per_position_pct": 2.0, "max_initial_aggregate_risk_pct": 1.0}})
    assert incoherent.status_code == 422
    live = c.put("/api/v1/settings", json={"changes": {"execution_mode": "live"}})
    assert live.status_code == 422
    same = c.put("/api/v1/settings", json={"changes": {"min_median_traded_value_cr": 1.0}})
    assert same.json()["saved"] is False
    hist = c.get("/api/v1/settings/history").json()["history"]
    assert hist[0]["changes"] == [{"path": "min_median_traded_value_cr", "from": 5.0, "to": 1.0}] and hist[0]["note"] == "demo data"
    s = c.get("/api/v1/settings").json()
    assert s["editable"] and s["strategy"]["min_median_traded_value_cr"] == 1.0 and s["defaults"]["min_median_traded_value_cr"] == 5.0


def test_scanner_cards_have_every_field(client):
    c, days = client
    r = c.get("/api/v1/scanner", params={"date": days[-1].isoformat()}).json()
    assert r["count"] > 0 and "no out-of-sample edge" in r["note"]
    card = r["stocks"][0]
    for k in ("symbol", "price", "data", "score", "band", "rs_rank", "rvol", "trend", "setup", "reasons", "sector", "eligible"):
        assert k in card
    scores = [s["score"] or -1 for s in r["stocks"]]
    assert scores == sorted(scores, reverse=True)
    with_setup = [s for s in r["stocks"] if s["setup"]]
    if with_setup:
        st = with_setup[0]["setup"]
        assert st["stop"] < st["entry"] and st["risk_pct"] > 0


def test_candles_market_overview_accounts_summary(client):
    c, days = client
    sym = c.get("/api/v1/scanner").json()["stocks"][0]["symbol"]
    cd = c.get(f"/api/v1/stocks/{sym}/candles", params={"days": 60}).json()
    assert len(cd["candles"]) == 60 and {"time", "open", "high", "low", "close", "volume", "ema20", "ema50"} <= set(cd["candles"][-1])
    assert c.get("/api/v1/stocks/NOPE/candles").status_code == 404
    mo = c.get("/api/v1/market/overview", params={"days": 20}).json()
    assert mo["latest"]["regime"] and len(mo["history"]) == 20
    assert c.get("/api/v1/accounts").json()["mode"] == "paper"
    sm = c.get("/api/v1/dashboard/summary").json()
    assert sm["session_date"] == days[-1].isoformat() and len(sm["top"]) <= 5 and sm["regime"]["policy"]
    sec = c.get("/api/v1/sectors", params={"history": 10}).json()
    assert isinstance(sec["rank_history"], dict)


def test_health_reports_latest_session(client):
    c, _ = client
    h = c.get("/api/health").json()
    assert h["execution_mode"] == "paper" and "latest_session" in h
