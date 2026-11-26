"""Phase 6 integration: the paper daily cycle against controlled prices in the test database."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select, text

from app.config import ExitConfig, StrategyConfig, UniverseConfig

pytestmark = pytest.mark.db

START = date(2026, 1, 5)
N = 40
SESSIONS = [START + timedelta(days=i) for i in range(70) if (START + timedelta(days=i)).weekday() < 5][:N]
BULL = {"allow_new_entries": True, "max_new_entries_per_day": 3, "risk_multiplier": 1.0, "min_score": 0}
BEAR = {"allow_new_entries": False, "max_new_entries_per_day": 0, "risk_multiplier": 0.0, "min_score": 100}


def cfg(**exits):
    return StrategyConfig(universe_rules=UniverseConfig(min_history_sessions=20), min_median_traded_value_cr=0,
                          exits=ExitConfig(**exits) if exits else ExitConfig())


def flat(price=100.0, n=N):
    return [(price, price * 1.01, price * 0.99, price)] * n


@pytest.fixture()
def world():
    """Seed stocks, sessions, raw+adjusted candles, universe snapshots and regimes directly."""
    from alembic import command
    from alembic.config import Config
    from app.database import ping, session_scope
    ok, detail = ping()
    if not ok:
        pytest.skip(detail)
    command.upgrade(Config("alembic.ini"), "head")
    with session_scope() as db:
        db.execute(text("truncate trade_journal, risk_events, portfolio_snapshots, paper_orders, paper_positions, trade_proposals, "
                        "paper_accounts, momentum_scores, relative_strength, sector_data, market_regime, stock_universe_snapshots, "
                        "technical_indicators, surveillance_flags, market_candles, corporate_actions, data_quality_issues, "
                        "stock_symbol_history, stocks, trading_sessions, data_provider_logs restart identity cascade"))

    def seed(paths: dict[str, list], sectors: dict[str, str] | None = None, regime=None, c=None):
        from app.models import MarketCandle, MarketRegime, Stock, TradingSession, UniverseSnapshot
        from app.universe.engine import config_hash
        from app.market_data.calendar import session_bar_start
        c = c or cfg()
        h = config_hash(c)
        ids = {}
        with session_scope() as db:
            for d in SESSIONS:
                db.add(TradingSession(session_date=d, source="nse_archive"))
                pol = (regime or {}).get(d, BULL)
                db.add(MarketRegime(session_date=d, regime="strong_bullish" if pol["allow_new_entries"] else "strong_bearish",
                                    score=60, coverage=1, components={}, policy=pol, version="2"))
            for i, (sym, path) in enumerate(paths.items()):
                st = Stock(key=f"INE{i:03d}T01011", isin=f"INE{i:03d}T01011", symbol=sym, kind="equity", status="active",
                           sector=(sectors or {}).get(sym, f"Sector{i}"))
                db.add(st)
                db.flush()
                ids[sym] = st.id
                for d, (o, hi, lo, cl) in zip(SESSIONS, path):
                    if o is None:
                        continue
                    for adj in (False, True):
                        db.add(MarketCandle(stock_id=st.id, interval="1d", ts=session_bar_start(d), session_date=d, open=o, high=hi,
                                            low=lo, close=cl, volume=1_000_000, turnover_inr=cl * 1e6, is_adjusted=adj,
                                            source="nse_archive", licence="exchange_official"))
                    db.add(UniverseSnapshot(session_date=d, stock_id=st.id, eligible=True, reasons=[], symbol=sym, series="EQ",
                                            sector=st.sector, close=cl, median_value_cr=60, history_sessions=100,
                                            config_hash=h, source="nse_archive"))
        return ids
    return seed, session_scope


def run(scope, c, upto: int, start: int = 0):
    from app.portfolio import service as svc
    out = []
    for d in SESSIONS[start:upto + 1]:
        with scope() as db:
            acct = svc.account_by_name(db, "t")
            if acct.last_processed and d <= acct.last_processed:
                continue
            out.append(svc.run_day(db, acct, d, c))
    return out


def account(scope, capital=100_000):
    from app.portfolio import service as svc
    with scope() as db:
        svc.create_account(db, "t", capital)


def propose(scope, c, sym, entry, stop, day, target=None):
    from app.portfolio import service as svc
    with scope() as db:
        return svc.propose(db, svc.account_by_name(db, "t"), sym, entry, stop, target=target, session_date=SESSIONS[day], cfg=c).id


def positions(scope):
    from app.models import PaperPosition
    with scope() as db:
        return [(p.symbol, p.status, p.qty, p.avg_price, p.stop, p.realised_pnl) for p in
                db.execute(select(PaperPosition).order_by(PaperPosition.id)).scalars()]


def test_entry_fills_next_open_with_slippage_and_charges_and_is_idempotent(world):
    from app.models import PaperOrder, RiskEvent, TradeJournal
    from app.portfolio import service as svc
    seed, scope = world
    c = cfg()
    seed({"ALPHA": flat(100) [:25] + [(102, 103, 101, 102)] * 15})
    account(scope)
    run(scope, c, 19)
    pid = propose(scope, c, "ALPHA", 100, 95, 20)
    assert propose(scope, c, "ALPHA", 100, 95, 20) == pid           # duplicate proposal -> same row
    out = run(scope, c, 21)
    assert out[0]["proposals"][0]["approved"] and out[0]["proposals"][0]["qty"] == 99
    with scope() as db:
        assert db.scalar(select(func.count()).select_from(RiskEvent).where(RiskEvent.proposal_id == pid)) == 10
        o = db.execute(select(PaperOrder)).scalars().one()
        assert o.status == "filled" and o.filled_session == SESSIONS[21] and o.fill_price == 100.05 and o.slippage_bps == 5
        assert o.charges["total"] == pytest.approx(o.charges["stt"] + o.charges["exchange_txn"] + o.charges["sebi_fee"]
                                                   + o.charges["stamp_duty"] + o.charges["gst"], abs=0.02)
        assert db.scalar(select(func.count()).select_from(TradeJournal).where(TradeJournal.action == "buy_filled")) == 1
    with scope() as db:
        with pytest.raises(svc.AlreadyProcessed):
            svc.run_day(db, svc.account_by_name(db, "t"), SESSIONS[21], c)


def test_stop_and_gap_through_stop_fill_correctly_and_books_reconcile(world):
    from app.models import PaperAccount, PortfolioSnapshot
    seed, scope = world
    c = cfg()
    a = flat(100, 22) + [(99, 100, 94, 95)] + flat(95, 17)                   # day 22: low 94 < stop 95
    b = flat(50, 22) + [(45, 46, 44, 45)] + flat(45, 17)                     # day 22: opens 45, below stop 47.5
    seed({"ALPHA": a, "BETA": b})
    account(scope)
    run(scope, c, 19)
    propose(scope, c, "ALPHA", 100, 95, 20)
    propose(scope, c, "BETA", 50, 47.5, 20)
    run(scope, c, 23)
    pos = {p[0]: p for p in positions(scope)}
    assert pos["ALPHA"][1] == "closed" and pos["BETA"][1] == "closed"
    from app.models import PaperOrder
    with scope() as db:
        fills = {o.symbol: o.fill_price for o in db.execute(select(PaperOrder).where(PaperOrder.kind == "stop")).scalars()}
        assert fills["ALPHA"] == 94.95       # stop 95 less 5 bps, ticked
        assert fills["BETA"] == 45.0         # gapped below the stop: open 45 less 5 bps -> 44.98 -> tick 45.00
        acct = db.execute(select(PaperAccount)).scalars().one()
        snaps = db.execute(select(PortfolioSnapshot).order_by(PortfolioSnapshot.session_date)).scalars().all()
        assert all(s.reconciliation["ok"] for s in snaps)
        assert snaps[-1].equity == pytest.approx(100_000 + acct.realised_pnl, abs=0.01)
        assert acct.realised_pnl < 0 and acct.total_charges > 0


def test_partial_target_breakeven_and_trailing_stop(world):
    seed, scope = world
    c = cfg()
    rally = [(100 + 2 * k, 101 + 2 * k, 99 + 2 * k, 100 + 2 * k) for k in range(1, 19)]  # steady climb to 136
    seed({"ALPHA": flat(100, 22) + rally})
    account(scope)
    run(scope, c, 19)
    propose(scope, c, "ALPHA", 100, 95, 20)
    run(scope, c, N - 1)
    from app.models import PaperOrder, TradeJournal
    with scope() as db:
        partial = db.execute(select(PaperOrder).where(PaperOrder.kind == "partial_target")).scalars().one()
        # fill 100.05, risk 5.05/share -> target 110.15; filled at the higher of open and target
        assert partial.qty == 33 and partial.fill_price >= 110.1
        raised = db.execute(select(TradeJournal).where(TradeJournal.action == "stop_raised")).scalars().all()
        assert raised and all(j.details["new"] > j.details["old"] for j in raised)
    sym, status, qty, avg, stop, _ = positions(scope)[0]
    assert status == "open" and qty == 66 and stop > avg   # breakeven and trailing above entry


def test_entry_cancelled_on_gap_up_beyond_chase_limit_or_open_below_stop(world):
    from app.models import PaperOrder
    seed, scope = world
    c = cfg()
    seed({"UP": flat(100, 21) + [(104, 105, 103, 104)] + flat(104, 18),     # +4% > 3% chase limit
          "DOWN": flat(100, 21) + [(94, 95, 93, 94)] + flat(94, 18)})       # opens below the 95 stop
    account(scope)
    run(scope, c, 19)
    propose(scope, c, "UP", 100, 95, 20)
    propose(scope, c, "DOWN", 100, 95, 20)
    run(scope, c, 22)
    with scope() as db:
        orders = {o.symbol: (o.status, o.reason) for o in db.execute(select(PaperOrder)).scalars()}
    assert orders["UP"][0] == "cancelled" and "above planned entry" in orders["UP"][1]
    assert orders["DOWN"][0] == "cancelled" and "below stop" in orders["DOWN"][1]
    assert positions(scope) == []


def test_split_while_held_adjusts_quantity_and_levels(world):
    from app.models import CorporateAction
    seed, scope = world
    c = cfg()
    path = flat(100, 25) + [(50, 50.5, 49.5, 50)] * 15
    ids = seed({"ALPHA": path})
    with scope() as db:
        db.add(CorporateAction(stock_id=ids["ALPHA"], ex_date=SESSIONS[25], kind="split_or_bonus", factor=0.5,
                               source="nse_archive:prev_close", confidence="high", details={}))
    account(scope)
    run(scope, c, 19)
    propose(scope, c, "ALPHA", 100, 95, 20)
    run(scope, c, 27)
    sym, status, qty, avg, stop, _ = positions(scope)[0]
    assert status == "open" and qty == 198 and avg == pytest.approx(50.025) and stop == pytest.approx(47.5)


def test_bearish_regime_rejects_with_every_check_logged(world):
    from app.models import RiskEvent, TradeProposal
    seed, scope = world
    c = cfg()
    seed({"ALPHA": flat(100)}, regime={d: BEAR for d in SESSIONS})
    account(scope)
    run(scope, c, 19)
    pid = propose(scope, c, "ALPHA", 100, 95, 20)
    run(scope, c, 20)
    with scope() as db:
        p = db.get(TradeProposal, pid)
        assert p.status == "rejected" and "does not allow" in p.decision["reason"]
        ev = db.execute(select(RiskEvent.check, RiskEvent.outcome).where(RiskEvent.proposal_id == pid).order_by(RiskEvent.id)).all()
        assert [e[1] for e in ev] == ["pass", "reject"] + ["skipped"] * 8


def test_time_stop_and_manual_exit_fill_at_next_open(world):
    from app.models import PaperOrder, PaperPosition
    from app.portfolio import service as svc
    seed, scope = world
    c = cfg(time_stop_sessions=5, partial_exit_r=None)
    seed({"ALPHA": flat(100), "BETA": flat(50)})
    account(scope)
    run(scope, c, 19)
    propose(scope, c, "ALPHA", 100, 95, 20)
    propose(scope, c, "BETA", 50, 47.5, 20)
    run(scope, c, 22)
    with scope() as db:
        acct = svc.account_by_name(db, "t")
        beta = db.execute(select(PaperPosition).where(PaperPosition.symbol == "BETA")).scalars().one()
        svc.manual_exit(db, acct, beta.id, "rebalance")
    run(scope, c, 30)
    with scope() as db:
        kinds = {(o.symbol, o.kind): (o.status, o.filled_session) for o in db.execute(select(PaperOrder)).scalars()}
    assert kinds[("BETA", "manual_exit")] == ("filled", SESSIONS[23])
    status, filled = kinds[("ALPHA", "time_stop")]
    assert status == "filled" and filled == SESSIONS[26]   # 5 sessions held at the close of day 25, sold at day 26's open


def test_drawdown_alerts_fire_when_a_gap_jumps_the_stops(world):
    """Stops cap each loss at ~0.5%; only a gap through them can push the account past 3% and 5%."""
    from app.models import RiskEvent
    seed, scope = world
    c = cfg(partial_exit_r=None)
    gap = flat(100, 24) + [(85, 86, 84, 85)] + flat(85, 15)   # day 24 opens 15% lower, below every 95 stop
    names = ["A", "B", "C", "D", "E"]
    seed({n: gap for n in names})
    account(scope)
    run(scope, c, 19)
    for n in names[:3]:
        propose(scope, c, n, 100, 95, 20)
    run(scope, c, 21)
    for n in names[3:]:
        propose(scope, c, n, 100, 95, 21)   # max 3 new entries per day in this regime
    run(scope, c, 30)
    with scope() as db:
        alerts = db.execute(select(RiskEvent).where(RiskEvent.check == "drawdown_alert").order_by(RiskEvent.id)).scalars().all()
    assert [a.details["level_pct"] for a in alerts] == [3.0, 5.0]
    assert {a.session_date for a in alerts} == {SESSIONS[24]} and alerts[-1].details["drawdown_pct"] > 5
    assert all(p[1] == "closed" for p in positions(scope))



def test_late_proposals_are_checked_at_once_and_stale_ones_expire(world):
    from app.models import TradeProposal
    from app.portfolio import service as svc
    seed, scope = world
    c = cfg()
    seed({"A": flat(100), "B": flat(100)})
    account(scope)
    run(scope, c, 20)
    late = propose(scope, c, "A", 100, 95, 20)          # day 20 already processed -> evaluated immediately
    with scope() as db:
        assert db.get(TradeProposal, late).status == "ordered"
        with pytest.raises(ValueError, match="before the last processed"):
            svc.propose(db, svc.account_by_name(db, "t"), "B", 100, 95, session_date=SESSIONS[19], cfg=c)
        # a pending row left from an earlier session (e.g. inserted by another tool) expires on the next run
        db.add(TradeProposal(account_id=svc.account_by_name(db, "t").id, stock_id=db.get(TradeProposal, late).stock_id + 1,
                             symbol="B", strategy="manual", session_date=SESSIONS[19], entry=100, stop=95,
                             idempotency_key="stale-test"))
    run(scope, c, 21)
    with scope() as db:
        stale = db.execute(select(TradeProposal).where(TradeProposal.idempotency_key == "stale-test")).scalars().one()
        assert stale.status == "expired" and "not evaluated on its session" in stale.decision["reason"]



def test_portfolio_api_round_trip(world):
    from fastapi.testclient import TestClient
    from app.main import create_app
    seed, scope = world
    c = cfg()
    seed({"ALPHA": flat(100), "BETA": flat(50)})
    account(scope)
    run(scope, c, 20)
    from app.config import default_strategy_config
    import app.api.portfolio as api_mod
    api_mod.default_strategy_config = lambda: c     # the test world uses relaxed universe rules
    try:
        client = TestClient(create_app())
        r = client.post("/api/v1/portfolio/proposals", params={"account": "t"}, json={"symbol": "ALPHA", "entry": 100, "stop": 95})
        assert r.status_code == 201 and r.json()["status"] == "ordered" and "PAPER" in r.json()["notice"]
        bad = client.post("/api/v1/portfolio/proposals", params={"account": "t"}, json={"symbol": "NOPE", "entry": 100, "stop": 95})
        assert bad.status_code == 422
    finally:
        api_mod.default_strategy_config = default_strategy_config
    run(scope, c, 22)
    client = TestClient(create_app())
    pf = client.get("/api/v1/portfolio", params={"account": "t"}).json()
    assert pf["mode"] == "paper" and pf["positions"][0]["symbol"] == "ALPHA" and pf["snapshot"]["reconciliation"]["ok"]
    props = client.get("/api/v1/portfolio/proposals", params={"account": "t"}).json()["proposals"]
    assert len(props[0]["risk_checks"]) == 10
    pid = pf["positions"][0]["id"]
    ex = client.post(f"/api/v1/portfolio/positions/{pid}/exit", params={"account": "t"}, json={"reason": "test exit"})
    assert ex.status_code == 201
    assert client.get("/api/v1/portfolio/journal", params={"account": "t"}).json()["journal"][0]["action"] == "exit_order"
    assert client.get("/api/v1/portfolio", params={"account": "missing"}).status_code == 404



def test_gap_up_fill_is_recapped_to_the_risk_budget(world):
    from app.models import PaperOrder, PortfolioSnapshot
    seed, scope = world
    c = cfg()
    seed({"ALPHA": flat(100, 21) + [(102.9, 104, 102, 103)] + flat(103, 18)})  # opens 2.9% higher: inside the chase limit
    account(scope)
    run(scope, c, 19)
    propose(scope, c, "ALPHA", 100, 95, 20)
    run(scope, c, 22)
    with scope() as db:
        o = db.execute(select(PaperOrder)).scalars().one()
        # approved 99 at the planned 100.05; filled at 102.95 -> 7.95/share risk -> 500 / 7.95 = 62 shares
        assert o.status == "filled" and o.qty == 62 and "position_risk limit" in o.reason
        snap = db.execute(select(PortfolioSnapshot).where(PortfolioSnapshot.session_date == SESSIONS[21])).scalars().one()
        assert snap.aggregate_initial_risk <= 500 + 0.01
