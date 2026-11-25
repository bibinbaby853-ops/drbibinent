"""Integration: ingest into the PostgreSQL test database (momentum_test), migrated with Alembic."""

from datetime import date

import pytest
from sqlalchemy import func, select, text

from app.database import ping, session_scope
from app.market_data import nse_archive as n
from app.market_data.demo import SPLIT_SYMBOL, DemoProvider
from app.market_data.ingest import confirm_inferred, ingest_range, ingest_session, refresh_adjustments
from app.models import CorporateAction, DataProviderLog, DataQualityIssue, MarketCandle, Stock, StockSymbolHistory, TradingSession
from tests.fixtures.nse import FakeHttp, bar, full_csv, udiff_csv, zipped

pytestmark = pytest.mark.db

TABLES = "market_candles, corporate_actions, data_quality_issues, stock_symbol_history, stocks, trading_sessions, data_provider_logs"


@pytest.fixture(scope="module", autouse=True)
def migrated():
    ok, detail = ping()
    if not ok:
        pytest.skip(f"PostgreSQL test database not reachable ({detail})")
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clean():
    with session_scope() as db:
        db.execute(text(f"truncate {TABLES} restart identity cascade"))
    yield


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def candle(db, symbol, d, adjusted):
    return db.execute(select(MarketCandle).join(Stock).where(Stock.symbol == symbol, MarketCandle.session_date == d,
                                                             MarketCandle.is_adjusted.is_(adjusted))).scalar_one()


# ------------------------------------------------------------------ demo

def test_demo_ingest_is_idempotent_and_adjusts_the_split():
    p = DemoProvider(end=date(2025, 1, 10))
    i = p.sessions.index(p.split_date)
    start, end = p.sessions[i - 7], p.sessions[i + 7]  # 15 sessions around the demo split
    for _ in range(2):  # second pass must not change anything
        ingest_range(session_scope, p, start, end)
        with session_scope() as db:
            refresh_adjustments(db, source="demo")
    with session_scope() as db:
        sessions = count(db, TradingSession, TradingSession.source == "demo")
        assert sessions == 15
        assert count(db, Stock) == 24 and count(db, Stock, Stock.demo.is_(False)) == 0
        assert count(db, MarketCandle, MarketCandle.is_adjusted.is_(False)) == 24 * 15
        assert count(db, MarketCandle, MarketCandle.is_adjusted.is_(True)) == 24 * 15
        assert count(db, MarketCandle, MarketCandle.demo.is_(False)) == 0
        ca = db.execute(select(CorporateAction)).scalars().all()
        assert [(c.ex_date, c.factor) for c in ca] == [(p.split_date, 0.5)]
        before = p.sessions[i - 1]
        raw, adj = candle(db, SPLIT_SYMBOL, before, False), candle(db, SPLIT_SYMBOL, before, True)
        assert adj.close == pytest.approx(raw.close * 0.5) and adj.volume == raw.volume * 2
        after = candle(db, SPLIT_SYMBOL, p.split_date, True)
        assert after.close == candle(db, SPLIT_SYMBOL, p.split_date, False).close


# ------------------------------------------------------------------ NSE-format fixtures

D1, D2, D3, D4 = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)
GA1, GA2 = "INE000G01011", "INE000G01029"


def sessions():
    return {
        D1: [bar("ALPHA", "INE000A01011", 100, 102, 99, 101, 100, 1000),
             bar("BETA", "INE000B01011", 50, 51, 49, 50, 50, 1000),
             bar("GAMMA", GA1, 200, 202, 198, 200, 199, 1000),
             bar("DELTA", "INE000D01011", 160, 163, 159, 162.65, 155, 1000)],
        D2: [bar("ALPHANEW", "INE000A01011", 101, 103, 100, 102, 101, 1000),          # symbol change, same ISIN
             bar("BETA", "INE000B01011", 50, 49, 48, 50, 50, 1000),                  # high < open: blocking
             bar("GAMMA", GA2, 101, 103, 100, 102, 100, 2000),                       # 1:2 split, new ISIN, adjusted PREV_CLOSE
             bar("DELTA", "INE000D01011", 162, 164, 161, 162.66, 162.65, 1000)],
        D4: [bar("ALPHANEW", "INE000A01011", 102, 104, 101, 103, 102, 1000),
             bar("BETA", "INE000B01011", 50, 51, 49, 50.5, 50, 1000),
             bar("GAMMA", GA2, 102, 104, 101, 103, 102, 1000),
             bar("DELTA", "INE000D01011", 17.0, 17.4, 16.5, 17.08, 162.66, 10000)],  # 1:10, PREV_CLOSE NOT adjusted
    }


def provider(tmp_path):
    files = {}
    for d, rows in sessions().items():
        files[n.udiff_url(d)] = zipped("b.csv", udiff_csv(d, rows))
        files[n.full_bhav_url(d)] = full_csv(d, rows)
    files[n.full_bhav_url(D3)] = full_csv(D2, sessions()[D2])  # holiday placeholder: previous session's file
    return n.NSEArchiveProvider(tmp_path, http=FakeHttp(files))


def run(p):
    out = ingest_range(session_scope, p, D1, D4)
    with session_scope() as db:
        refresh_adjustments(db, source="nse_archive")
    return out


def test_nse_ingest_identity_quality_and_adjustments(tmp_path):
    results = run(provider(tmp_path))
    assert [r.status for r in results] == ["ingested", "ingested", "holiday", "ingested"]
    assert results[1].symbol_changes == 1 and results[1].isin_changes == 1 and results[1].rejected == 1

    with session_scope() as db:
        assert count(db, Stock) == 4  # ISIN change and symbol change did not create new stocks
        gamma = db.execute(select(Stock).where(Stock.symbol == "GAMMA")).scalar_one()
        assert gamma.isin == GA2 and gamma.key == GA2 and gamma.first_seen == D1
        reasons = {(h.symbol, h.reason) for h in db.execute(select(StockSymbolHistory)).scalars()}
        assert {("ALPHANEW", "symbol_change"), ("GAMMA", "isin_change")} <= reasons

        # BETA's bad bar was not stored but the issue was
        assert count(db, MarketCandle, MarketCandle.session_date == D2, MarketCandle.is_adjusted.is_(False)) == 3
        assert count(db, DataQualityIssue, DataQualityIssue.check == "ohlc_inconsistent", DataQualityIssue.symbol == "BETA") == 1
        assert count(db, TradingSession, TradingSession.source == "nse_archive") == 3  # holiday is not a session

        acts = {c.ex_date: c for c in db.execute(select(CorporateAction)).scalars()}
        assert acts[D2].factor == pytest.approx(0.5) and acts[D2].confidence == "high"
        assert acts[D4].factor == pytest.approx(0.1) and acts[D4].confidence == "medium"
        assert count(db, DataQualityIssue, DataQualityIssue.check == "corporate_action_inferred") == 1

        assert candle(db, "GAMMA", D1, True).close == pytest.approx(100)  # 200 * 0.5
        assert candle(db, "DELTA", D1, True).close == pytest.approx(16.265)  # 162.65 * 0.1
        assert candle(db, "DELTA", D2, True).volume == 10000
        assert candle(db, "DELTA", D4, True).close == pytest.approx(17.08)

        logs = db.execute(select(DataProviderLog)).scalars().all()
        assert {l.dataset for l in logs} == {"udiff", "full_bhav"}
        assert all(l.licence == "exchange_official" and len(l.content_sha256) == 64 for l in logs)
        assert db.scalar(select(func.min(MarketCandle.dataset_id))) is not None


def test_nse_reingest_is_idempotent(tmp_path):
    p = provider(tmp_path)
    run(p)
    with session_scope() as db:
        before = {m.__name__: count(db, m) for m in (Stock, MarketCandle, CorporateAction, StockSymbolHistory, DataQualityIssue, TradingSession)}
    run(p)
    with session_scope() as db:
        after = {m.__name__: count(db, m) for m in (Stock, MarketCandle, CorporateAction, StockSymbolHistory, DataQualityIssue, TradingSession)}
    assert before == after


def test_second_source_confirms_or_rejects_inferred_factor(tmp_path):
    run(provider(tmp_path))
    with session_scope() as db:
        out = confirm_inferred(db, lambda sym: [(D4, 10.0)] if sym == "DELTA" else [], source="nse_archive")
    assert out["confirmed"] == 1
    with session_scope() as db:
        assert db.execute(select(CorporateAction).where(CorporateAction.ex_date == D4)).scalar_one().confidence == "high"


def test_second_source_ratio_that_cannot_explain_the_gap_does_not_override(tmp_path):
    run(provider(tmp_path))
    with session_scope() as db:
        out = confirm_inferred(db, lambda sym: [(D4, 2.0)], source="nse_archive")  # 1:2 cannot explain 162.66 -> 17
    assert out["disagreed"] == 1
    with session_scope() as db:
        ca = db.execute(select(CorporateAction).where(CorporateAction.ex_date == D4)).scalar_one()
        assert ca.confidence == "medium" and ca.factor == pytest.approx(0.1)
        assert candle(db, "DELTA", D1, True).close == pytest.approx(16.265)  # still adjusted
        assert count(db, DataQualityIssue, DataQualityIssue.check == "corporate_action_disagreement") == 1


def test_second_source_ratio_that_explains_the_gap_replaces_the_inferred_one(tmp_path):
    run(provider(tmp_path))
    with session_scope() as db:
        out = confirm_inferred(db, lambda sym: [(D4, 9.5)], source="nse_archive")  # also fits within the bands
    assert out["replaced"] == 1
    with session_scope() as db:
        ca = db.execute(select(CorporateAction).where(CorporateAction.ex_date == D4)).scalar_one()
        assert ca.confidence == "high" and ca.factor == pytest.approx(1 / 9.5)
        assert candle(db, "DELTA", D1, True).close == pytest.approx(162.65 / 9.5)


def test_not_yet_published_today_is_not_called_a_holiday(tmp_path):
    from datetime import datetime
    from app.market_data.calendar import IST
    with session_scope() as db:
        r = ingest_session(db, provider(tmp_path), date(2026, 11, 25), now=datetime(2026, 11, 25, 12, 0, tzinfo=IST))
    assert r.status == "not_published"


def _market(n_stocks, d, prev_of, close_of):
    return [bar(f"S{i:03d}", f"INE{i:03d}X01011", close_of(i), close_of(i) + 1, close_of(i) - 1, close_of(i), prev_of(i), 1000)
            for i in range(n_stocks)]


def test_weekend_special_session_is_ingested(tmp_path):
    fri, sun, mon = date(2026, 1, 30), date(2026, 2, 1), date(2026, 2, 2)  # Budget-day Sunday session
    files = {n.udiff_url(fri): zipped("a.csv", udiff_csv(fri, _market(3, fri, lambda i: 100, lambda i: 100))),
             n.udiff_url(sun): zipped("b.csv", udiff_csv(sun, _market(3, sun, lambda i: 100, lambda i: 110))),
             n.udiff_url(mon): zipped("c.csv", udiff_csv(mon, _market(3, mon, lambda i: 110, lambda i: 111)))}
    res = ingest_range(session_scope, n.NSEArchiveProvider(tmp_path, http=FakeHttp(files)), fri, mon)
    assert [r.status for r in res] == ["ingested", "weekend", "ingested", "ingested"]
    with session_scope() as db:
        assert refresh_adjustments(db, source="nse_archive")["factors_new"] == 0


def test_market_wide_prev_close_gap_is_a_calendar_problem_not_corporate_actions(tmp_path):
    fri, mon = date(2026, 1, 30), date(2026, 2, 2)  # the Sunday file is "missing" here
    files = {n.udiff_url(fri): zipped("a.csv", udiff_csv(fri, _market(40, fri, lambda i: 100, lambda i: 100))),
             n.udiff_url(mon): zipped("c.csv", udiff_csv(mon, _market(40, mon, lambda i: 110, lambda i: 111)))}
    ingest_range(session_scope, n.NSEArchiveProvider(tmp_path, http=FakeHttp(files)), fri, mon)
    with session_scope() as db:
        out = refresh_adjustments(db, source="nse_archive")
        assert out["factors_new"] == 0 and out["market_wide_gap_dates"] == [mon.isoformat()]
        assert count(db, DataQualityIssue, DataQualityIssue.check == "market_wide_prev_close_gap",
                     DataQualityIssue.severity == "blocking") == 1


def test_etfs_are_stored_as_etf_not_equity(tmp_path):
    rows = [bar("ALPHA", "INE000A01011", 100, 102, 99, 101, 100, 1000), bar("GOLDX", "INF000X01011", 60, 61, 59, 60, 60, 1000)]
    files = {n.udiff_url(D1): zipped("a.csv", udiff_csv(D1, rows)), n.udiff_url(D2): zipped("b.csv", udiff_csv(D2, rows))}
    p = n.NSEArchiveProvider(tmp_path, http=FakeHttp(files))
    ingest_range(session_scope, p, D1, D2)
    with session_scope() as db:
        kinds = dict(db.execute(select(Stock.symbol, Stock.kind)).all())
        assert kinds == {"ALPHA": "equity", "GOLDX": "etf"} and count(db, Stock) == 2  # not re-created on day 2
