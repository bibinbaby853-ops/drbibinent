"""Phase 4 integration: demo data -> universe -> RS, sectors, regime, scores in the test database."""

from datetime import date

import pytest
from sqlalchemy import func, select, text

from app.config import StrategyConfig

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def demo_db():
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
    from app.universe.engine import build_snapshot
    with session_scope() as db:
        db.execute(text("truncate momentum_scores, relative_strength, sector_data, market_regime, stock_universe_snapshots, "
                        "technical_indicators, surveillance_flags, market_candles, corporate_actions, data_quality_issues, "
                        "stock_symbol_history, stocks, trading_sessions, data_provider_logs restart identity cascade"))
    p = DemoProvider(start=date(2024, 1, 1), end=date(2025, 6, 30))
    ingest_range(session_scope, p, p.start, p.end)
    cfg = StrategyConfig(min_median_traded_value_cr=1)
    with session_scope() as db:
        refresh_adjustments(db, source="demo")
        ingest_demo_index(db, p, p.start, p.end)
    for d in p.sessions[-30:]:
        with session_scope() as db:
            build_snapshot(db, d, cfg, source="demo")
    return p, cfg, session_scope


def test_pipeline_stores_rs_sectors_regime_and_scores_for_eligible_stocks_only(demo_db):
    from app.models import MarketRegime, MomentumScore, RelativeStrength, SectorData, UniverseSnapshot
    from app.scoring.pipeline import run_range, scoring_hash
    p, cfg, scope = demo_db
    days = p.sessions[-10:]
    with scope() as db:
        res = run_range(db, days[0], days[-1], cfg, detail_sessions=2, source="demo")
    assert res.sessions == 10 and sum(res.regimes.values()) == 10
    with scope() as db:
        for d in days:
            elig = set(db.execute(select(UniverseSnapshot.stock_id).where(UniverseSnapshot.session_date == d,
                                                                          UniverseSnapshot.eligible.is_(True))).scalars())
            scored = set(db.execute(select(MomentumScore.stock_id).where(MomentumScore.session_date == d)).scalars())
            assert scored == elig and len(scored) > 0          # gate: only eligible stocks are scored
            ranks = db.execute(select(RelativeStrength.rs_rank).where(RelativeStrength.session_date == d)).scalars().all()
            assert min(ranks) >= 1 and max(ranks) <= 99
        assert db.scalar(select(func.count()).select_from(MarketRegime)) == 10
        assert db.scalar(select(func.count()).select_from(SectorData)) > 0
        with_detail = db.execute(select(MomentumScore.session_date).where(MomentumScore.breakdown.is_not(None)).distinct()).scalars().all()
        assert sorted(with_detail) == days[-2:]
        row = db.execute(select(MomentumScore).where(MomentumScore.session_date == days[-1])).scalars().first()
        assert row.config_hash == scoring_hash(cfg) and 0 <= row.total <= 100 and len(row.breakdown) == 28
        assert sum(row.groups.values()) == pytest.approx(row.total * row.coverage, abs=0.05)


def test_scores_do_not_depend_on_the_run_range(demo_db):
    from app.models import MomentumScore, RelativeStrength
    from app.scoring.pipeline import run_range
    p, cfg, scope = demo_db
    d = p.sessions[-1]

    def snapshot():
        with scope() as db:
            sc = dict(db.execute(select(MomentumScore.stock_id, MomentumScore.total).where(MomentumScore.session_date == d)).all())
            rs = dict(db.execute(select(RelativeStrength.stock_id, RelativeStrength.rs_rank).where(RelativeStrength.session_date == d)).all())
        return sc, rs

    with scope() as db:
        run_range(db, p.sessions[-10], d, cfg, source="demo")
    a = snapshot()
    with scope() as db:
        run_range(db, d, d, cfg, source="demo")  # just the one day, re-run: same values, no duplicates
    assert snapshot() == a
    with scope() as db:
        assert db.scalar(select(func.count()).select_from(MomentumScore).where(MomentumScore.session_date == d)) == len(a[0])


def test_api_serves_regime_scores_and_breakdown(demo_db):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.scoring.pipeline import run_range
    p, cfg, scope = demo_db
    d = p.sessions[-1]
    with scope() as db:
        run_range(db, d, d, cfg, source="demo")
    c = TestClient(create_app())
    reg = c.get("/api/v1/market/regime", params={"date": d.isoformat()}).json()
    assert reg["regime"] in ("strong_bullish", "moderately_bullish", "sideways", "moderately_bearish", "strong_bearish")
    assert set(reg["policy"]) == {"allow_new_entries", "max_new_entries_per_day", "risk_multiplier", "min_score"}
    sc = c.get("/api/v1/scores", params={"date": d.isoformat(), "source": "demo", "config": "any"}).json()
    assert sc["session_date"] == d.isoformat() and sc["rows"]
    top = sc["rows"][0]["symbol"]
    detail = c.get(f"/api/v1/stocks/{top}/score", params={"date": d.isoformat(), "source": "demo"}).json()
    assert len(detail["breakdown"]) == 28 and {"id", "rule", "status", "points", "max", "value"} <= set(detail["breakdown"][0])
    secs = c.get("/api/v1/sectors", params={"date": d.isoformat()}).json()
    assert secs["session_date"] == d.isoformat()
