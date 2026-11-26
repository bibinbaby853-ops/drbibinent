from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import func, select, text

from app.config import StrategyConfig, UniverseConfig
from app.universe.engine import config_hash, evaluate

W = [date(2026, 11, 2) + timedelta(days=i) for i in range(20)]


def today(*rows):
    return pd.DataFrame(rows, columns=["stock_id", "symbol", "series", "sector", "close"])


def values(stock_id, per_day_cr, days=W):
    return pd.DataFrame({"stock_id": stock_id, "session_date": days, "turnover_inr": [per_day_cr * 1e7] * len(days)})


def run(cfg=None, t=None, v=None, history=None, **kw):
    cfg = cfg or StrategyConfig()
    t = t if t is not None else today((1, "A", "EQ", "IT", 100.0))
    v = v if v is not None else values(1, 10)
    history = history if history is not None else pd.Series({1: 500})
    args = dict(blocked=set(), market_gap=False, quarantined=set(), gsm={})
    args.update(kw)
    return evaluate(cfg, t, v, history, W, **args).set_index("stock_id")


def test_liquid_eq_stock_with_history_is_eligible():
    r = run().loc[1]
    assert r.eligible and r.reasons == [] and r.median_value_cr == 10


def test_liquidity_uses_median_with_untraded_sessions_as_zero():
    # traded 9 of 20 sessions at Rs 50 Cr: median of [0]*11 + [50]*9 is 0 -> illiquid
    r = run(v=values(1, 50, W[:9])).loc[1]
    assert not r.eligible and "illiquid" in r.reasons and r.median_value_cr == 0
    # threshold is configurable and inclusive
    assert run(cfg=StrategyConfig(min_median_traded_value_cr=10), v=values(1, 10)).loc[1].eligible
    assert not run(cfg=StrategyConfig(min_median_traded_value_cr=10.01), v=values(1, 10)).loc[1].eligible


def test_each_exclusion_reason():
    t = today((1, "A", "EQ", "IT", 100.0), (2, "B", "BE", "IT", 100.0), (3, "C", "EQ", "IT", 100.0),
              (4, "D", "EQ", "IT", 100.0), (5, "E", "EQ", "IT", 100.0), (6, "F", "EQ", "IT", 100.0))
    v = pd.concat([values(i, 10) for i in range(1, 7)])
    hist = pd.Series({1: 500, 2: 500, 3: 50, 4: 500, 5: 500, 6: 500})
    out = run(t=t, v=v, history=hist, blocked={4}, quarantined={5}, gsm={6: "I"})
    assert out.loc[1].eligible
    assert out.loc[2].reasons == ["series"]
    assert out.loc[3].reasons == ["insufficient_history"]
    assert out.loc[4].reasons == ["data_quality"]
    assert out.loc[5].reasons == ["unconfirmed_corporate_action"]
    assert out.loc[6].reasons == ["surveillance_gsm"]
    assert run(market_gap=True).loc[1].reasons == ["market_data_gap"]
    etf = today((7, "GOLDETF", "EQ", "Unclassified", 60.0)).assign(kind="etf")
    assert run(t=etf, v=values(7, 10), history=pd.Series({7: 500})).loc[7].reasons == ["not_equity"]


def test_rules_are_configurable():
    cfg = StrategyConfig(universe_rules=UniverseConfig(eligible_series=("EQ", "BE"), exclude_gsm=False, min_history_sessions=20))
    t = today((2, "B", "BE", "IT", 100.0))
    out = run(cfg=cfg, t=t, v=values(2, 10), history=pd.Series({2: 30}), gsm={2: "I"})
    assert out.loc[2].eligible


def test_config_hash_changes_with_rules_only():
    assert config_hash(StrategyConfig()) == config_hash(StrategyConfig(max_positions=3))  # not a universe rule
    assert config_hash(StrategyConfig()) != config_hash(StrategyConfig(min_median_traded_value_cr=4))


# ------------------------------------------------------------------ database (demo data)

@pytest.mark.db
class TestSnapshotsInDatabase:
    @pytest.fixture(autouse=True)
    def data(self):
        from app.database import ping, session_scope
        ok, detail = ping()
        if not ok:
            pytest.skip(detail)
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")
        from app.market_data.demo import DemoProvider
        from app.market_data.ingest import ingest_range, refresh_adjustments
        with session_scope() as db:
            db.execute(text("truncate stock_universe_snapshots, technical_indicators, surveillance_flags, market_candles, "
                            "corporate_actions, data_quality_issues, stock_symbol_history, stocks, trading_sessions, "
                            "data_provider_logs restart identity cascade"))
        self.p = DemoProvider(end=date(2024, 12, 31))
        i = self.p.sessions.index(self.p.split_date)
        self.sessions = self.p.sessions[i - 130:i + 10]
        ingest_range(session_scope, self.p, self.sessions[0], self.sessions[-1])
        with session_scope() as db:
            refresh_adjustments(db, source="demo")
        self.scope = session_scope

    def snapshot_rows(self, d, cfg):
        from app.models import UniverseSnapshot
        with self.scope() as db:
            rows = db.execute(select(UniverseSnapshot).where(UniverseSnapshot.session_date == d,
                                                             UniverseSnapshot.config_hash == config_hash(cfg))
                              .order_by(UniverseSnapshot.stock_id)).scalars().all()
            return [(r.stock_id, r.eligible, tuple(r.reasons), r.median_value_cr, r.history_sessions) for r in rows]

    def test_snapshot_is_reproducible_and_point_in_time(self):
        from app.market_data.ingest import ingest_range
        from app.universe.engine import build_snapshot
        cfg = StrategyConfig(min_median_traded_value_cr=1)
        d = self.sessions[120]
        with self.scope() as db:
            r1 = build_snapshot(db, d, cfg, source="demo")
        first = self.snapshot_rows(d, cfg)
        assert r1.candidates == 24 and len(first) == 24 and r1.eligible > 0
        # later data arrives; rebuilding the same day must give the identical snapshot
        later = self.p.sessions[self.p.sessions.index(self.sessions[-1]) + 1: self.p.sessions.index(self.sessions[-1]) + 15]
        ingest_range(self.scope, self.p, later[0], later[-1])
        with self.scope() as db:
            build_snapshot(db, d, cfg, source="demo")
        assert self.snapshot_rows(d, cfg) == first
        # a different rule set is stored alongside, not over it
        strict = StrategyConfig(min_median_traded_value_cr=1000)
        with self.scope() as db:
            r2 = build_snapshot(db, d, strict, source="demo")
        assert r2.eligible == 0 and r2.reasons["illiquid"] == 24
        assert self.snapshot_rows(d, cfg) == first

    def test_history_requirement_and_unclassified_sector(self):
        from app.universe.engine import build_snapshot
        from app.models import UniverseSnapshot
        cfg = StrategyConfig(min_median_traded_value_cr=0)
        with self.scope() as db:
            early = build_snapshot(db, self.sessions[50], cfg, source="demo")
            assert early.reasons.get("insufficient_history") == 24  # 51 sessions < 100
            ok = build_snapshot(db, self.sessions[110], cfg, source="demo")
            assert "insufficient_history" not in ok.reasons
            sectors = set(db.execute(select(UniverseSnapshot.sector).where(UniverseSnapshot.session_date == self.sessions[110])).scalars())
            from app.market_data.demo import DEMO_SECTORS
            assert sectors == set(DEMO_SECTORS)  # demo provider's synthetic sector labels
            # a stock without a stored sector is grouped as Unclassified (ADR-013)
            db.execute(text("update stocks set sector = null where symbol = 'DEMO01'"))
            build_snapshot(db, self.sessions[110], cfg, source="demo")
            row = db.execute(select(UniverseSnapshot.sector).where(UniverseSnapshot.session_date == self.sessions[110],
                                                                   UniverseSnapshot.symbol == "DEMO01")).scalar_one()
            assert row == "Unclassified"

    def test_indicators_for_eligible_stocks_use_point_in_time_prices(self):
        from app.universe.indicators import compute_for_session
        from app.models import IndicatorSnapshot, MarketCandle, Stock
        from app.market_data.demo import SPLIT_SYMBOL
        i = self.sessions.index(self.p.split_date)
        before, after = self.sessions[i - 1], self.sessions[i + 5]
        with self.scope() as db:
            sid = db.execute(select(Stock.id).where(Stock.symbol == SPLIT_SYMBOL)).scalar_one()
            raw_before = db.execute(select(MarketCandle.close).where(MarketCandle.stock_id == sid, MarketCandle.session_date == before,
                                                                     MarketCandle.is_adjusted.is_(False))).scalar_one()
            v_before = compute_for_session(db, before, [sid], source="demo")[sid]
            v_after = compute_for_session(db, after, [sid], source="demo")[sid]
            # before the ex-date the split is unknown: levels are the raw price, no adjustment
            assert v_before["close"] == pytest.approx(raw_before)
            # after it, the 20-day average sits near the post-split price, not double it
            assert v_after["sma_20"] == pytest.approx(v_after["close"], rel=0.25)
            assert db.scalar(select(func.count()).select_from(IndicatorSnapshot)) == 2

    def test_api_serves_stored_snapshot_and_indicators(self):
        from fastapi.testclient import TestClient
        from app.config import default_strategy_config
        from app.main import create_app
        from app.universe.engine import build_snapshot
        from app.universe.indicators import compute_for_session
        from app.indicators.panel import INDICATOR_VERSION
        d = self.sessions[-1]
        with self.scope() as db:
            r = build_snapshot(db, d, default_strategy_config(), source="demo")
            from app.models import UniverseSnapshot
            ids = db.execute(select(UniverseSnapshot.stock_id).where(UniverseSnapshot.session_date == d,
                                                                     UniverseSnapshot.eligible.is_(True))).scalars().all()
            compute_for_session(db, d, list(ids), source="demo")
        c = TestClient(create_app())
        u = c.get("/api/v1/universe", params={"source": "demo"}).json()
        assert u["session_date"] == d.isoformat() and u["demo"] is True and u["eligible"] == r.eligible
        assert u["candidates"] == 24 and all(s["eligible"] for s in u["stocks"])
        if u["stocks"]:
            sym = u["stocks"][0]["symbol"]
            ind = c.get(f"/api/v1/stocks/{sym}/indicators", params={"source": "demo"}).json()
            assert ind["session_date"] == d.isoformat() and "rsi_14" in ind["values"] and ind["indicator_version"] == INDICATOR_VERSION
