"""Phase 5 integration: signal pipeline on demo data; reproducibility; hand-off to the paper portfolio."""

from datetime import date

import pytest
from sqlalchemy import func, select, text

from app.config import StrategyConfig

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def demo():
    from alembic import command
    from alembic.config import Config
    from app.database import ping, session_scope
    ok, detail = ping()
    if not ok:
        pytest.skip(detail)
    command.upgrade(Config("alembic.ini"), "head")
    from app.market_data.demo import DemoProvider
    from app.market_data.indices import ingest_demo_index
    from app.market_data.ingest import ingest_range, refresh_adjustments
    from app.scoring.pipeline import run_range
    from app.universe.engine import build_snapshot
    with session_scope() as db:
        db.execute(text("truncate signal_events, trade_signals, trade_journal, risk_events, portfolio_snapshots, paper_orders, "
                        "paper_positions, trade_proposals, paper_accounts, momentum_scores, relative_strength, sector_data, "
                        "market_regime, stock_universe_snapshots, technical_indicators, surveillance_flags, market_candles, "
                        "corporate_actions, data_quality_issues, stock_symbol_history, stocks, trading_sessions, data_provider_logs "
                        "restart identity cascade"))
    p = DemoProvider(n_stocks=30, start=date(2024, 1, 1), end=date(2025, 9, 30), seed=11)
    ingest_range(session_scope, p, p.start, p.end)
    cfg = StrategyConfig(min_median_traded_value_cr=1)
    with session_scope() as db:
        refresh_adjustments(db, source="demo")
        ingest_demo_index(db, p, p.start, p.end)
    days = p.sessions[-80:]
    for d in days:
        with session_scope() as db:
            build_snapshot(db, d, cfg, source="demo")
    with session_scope() as db:
        run_range(db, days[0], days[-1], cfg, source="demo", detail_sessions=1)
    return p, cfg, days, session_scope


def signals(scope, upto):
    from app.models import TradeSignal
    with scope() as db:
        return sorted((s.key, s.strategy, s.state if (s.closed and s.closed <= upto) else "open", s.confirmed, s.stop)
                      for s in db.execute(select(TradeSignal).where(TradeSignal.opened <= upto)).scalars()
                      if s.closed is None or s.closed <= upto or s.opened <= upto)


def test_signals_are_built_and_reproducible_when_data_extends(demo):
    from app.models import SignalEvent, TradeSignal
    from app.strategies.pipeline import build
    p, cfg, days, scope = demo
    mid = days[50]
    with scope() as db:
        out_mid = build(db, mid, cfg, source="demo")
    with scope() as db:
        early = {(s.key, s.strategy, s.confirmed, s.stop) for s in db.execute(select(TradeSignal).where(TradeSignal.closed <= mid)).scalars()}
    with scope() as db:
        out_end = build(db, days[-1], cfg, source="demo")
        later = {(s.key, s.strategy, s.confirmed, s.stop) for s in db.execute(select(TradeSignal).where(TradeSignal.closed <= mid)).scalars()}
        assert db.scalar(select(func.count()).select_from(SignalEvent)) > 0
    assert out_end["signals"] >= out_mid["signals"] > 0
    assert early == later                      # signals closed by mid are unchanged by later data


def test_one_active_signal_per_stock_and_valid_levels(demo):
    from app.models import TradeSignal
    p, cfg, days, scope = demo
    with scope() as db:
        rows = db.execute(select(TradeSignal)).scalars().all()
    open_per_stock = {}
    for s in rows:
        if s.is_open:
            open_per_stock[s.stock_id] = open_per_stock.get(s.stock_id, 0) + 1
        if s.confirmed:
            assert s.stop < s.entry and s.state == "confirmed"
    assert all(n == 1 for n in open_per_stock.values())
    # lifecycles of one stock never overlap
    by_stock = {}
    for s in sorted(rows, key=lambda r: r.opened):
        by_stock.setdefault(s.stock_id, []).append(s)
    for lst in by_stock.values():
        for a, b in zip(lst, lst[1:]):
            assert a.closed is not None and b.opened >= a.closed


def test_confirmed_signals_flow_into_paper_proposals(demo):
    from app.models import TradeProposal, TradeSignal
    from app.portfolio import service as svc
    from app.strategies.pipeline import strategy_hash
    p, cfg, days, scope = demo
    d = days[-1]
    with scope() as db:
        sid = db.execute(text("select id from stocks where demo and kind='equity' order by id limit 1")).scalar()
        close = db.execute(text("select close from market_candles where stock_id=:s and session_date=:d and not is_adjusted"),
                           {"s": sid, "d": d}).scalar()
        # a confirmed signal on the last session (inserted directly so the test does not depend on demo prices)
        sym = db.execute(text("select symbol from stocks where id = :s"), {"s": sid}).scalar()
        db.add(TradeSignal(key=f"test:{sid}", stock_id=sid, symbol=sym, strategy="breakout", state="confirmed", is_open=False,
                           opened=d, closed=d, confirmed=d, entry=close, stop=round(close * 0.95, 2), reasons={}, version="1",
                           config_hash=strategy_hash(cfg)))
        acct = svc.create_account(db, "sig", 100_000, source="demo")
        acct.last_processed = days[-2]
    with scope() as db:
        acct = svc.account_by_name(db, "sig")
        out = svc.run_day(db, acct, d, cfg, auto_signals=True)
        assert out["signal_proposals"]
        props = db.execute(select(TradeProposal).where(TradeProposal.account_id == acct.id)).scalars().all()
        assert any(pp.strategy == "breakout" and "signal" in (pp.note or "") for pp in props)
        assert all(pp.status in ("ordered", "rejected") for pp in props)   # every one went through the risk engine



def test_signal_api(demo):
    from fastapi.testclient import TestClient
    from app.main import create_app
    import app.api.signals as mod
    p, cfg, days, scope = demo
    orig = mod.default_strategy_config
    mod.default_strategy_config = lambda: cfg
    try:
        c = TestClient(create_app())
        r = c.get("/api/v1/signals", params={"date": days[-1].isoformat()}).json()
        assert r["session_date"] == days[-1].isoformat() and "risk engine" in r["note"]
        any_sym = next((s["symbol"] for s in r["signals"]), None)
        if any_sym:
            detail = c.get(f"/api/v1/stocks/{any_sym}/signals").json()
            assert detail["signals"] and any(s["events"] for s in detail["signals"])
        assert c.get("/api/v1/stocks/NOPE/signals").status_code == 404
    finally:
        mod.default_strategy_config = orig
