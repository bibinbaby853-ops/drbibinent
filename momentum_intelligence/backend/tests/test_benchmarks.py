"""ADR-018: official NSE index closes as the benchmark, Yahoo as fallback, carried values marked."""

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import delete, select, text

from app.market_data import nse_archive as n
from app.market_data.base import DataMismatch, DataNotPublished
from app.scoring.panel import align_benchmark
from tests.fixtures.nse import FakeHttp

D = date(2026, 11, 25)
HEADER = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,"
          "Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield")


def ind_csv(d: date, rows=None) -> bytes:
    ds = d.strftime("%d-%m-%Y")
    rows = rows or [f"Nifty 50,{ds},25658.35,25951.65,25619,25819.25,469.6,1.85,533250582,42910.7,21.03,3.17,1.32",
                    f"Nifty Bank,{ds},1,2,0.5,1.5,0,0,1,1,1,1,1",
                    f"Nifty 500,{ds},23988.3,24126.1,23884.15,24025.4,277.25,1.17,2440903049,106908.14,22.44,3.37,1.19",
                    f"India VIX,{ds},17.6975,18.2375,16.4275,17.67,-0.03,-.16,-,-,-,-,-"]
    return ("\n".join([HEADER, *rows]) + "\n").encode()


def test_parse_keeps_the_three_benchmarks_with_official_values():
    df = n.parse_index_close(ind_csv(D), D).set_index("symbol")
    assert sorted(df.index) == ["INDIA VIX", "NIFTY 50", "NIFTY 500"]          # Nifty Bank ignored
    assert df.loc["NIFTY 50", "close"] == 25819.25 and df.loc["NIFTY 500", "high"] == 24126.1
    assert df.loc["INDIA VIX", "volume"] == 0 and pd.isna(df.loc["INDIA VIX", "turnover_inr"])
    assert df.loc["NIFTY 50", "turnover_inr"] == pytest.approx(42910.7e7)
    assert (df["session_date"] == D).all() and df["ts"].iloc[0].hour == 9


def test_parse_rejects_another_session_and_files_without_benchmarks():
    with pytest.raises(DataMismatch):
        n.parse_index_close(ind_csv(date(2026, 11, 23)), D)
    with pytest.raises(ValueError):
        n.parse_index_close(ind_csv(D, [f"Nifty Bank,{D:%d-%m-%Y},1,2,0.5,1.5,0,0,1,1,1,1,1"]), D)


def test_provider_fetches_caches_and_reports_unpublished(tmp_path):
    http = FakeHttp({n.index_close_url(D): ind_csv(D)})
    p = n.NSEArchiveProvider(tmp_path, http=http)
    f = p.index_closes(D)
    assert len(f.data) == 3 and f.lineage[0].dataset == "index_close" and f.lineage[0].licence == "exchange_official"
    n.NSEArchiveProvider(tmp_path, http=FakeHttp({})).index_closes(D)          # served from the cache
    with pytest.raises(DataNotPublished):
        p.index_closes(date(2026, 11, 24))                                      # holiday: 404


def test_carried_sessions_are_marked_and_limited():
    sessions = [date(2026, 11, d) for d in (17, 18, 19, 20, 23, 24, 25, 26, 27)]
    close = pd.Series([1.0, 2.0, 3.0], index=[sessions[0], sessions[1], sessions[3]])
    origin = pd.Series(["nse", "nse", "yahoo"], index=close.index)
    filled, o = align_benchmark(close, origin, sessions)
    assert list(o[:4]) == ["nse", "nse", "carried", "yahoo"]
    assert filled[sessions[2]] == 2.0                                            # previous close, marked carried
    assert list(o[4:7]) == ["carried"] * 3 and pd.isna(filled[sessions[7]])     # beyond 3 sessions: missing, not guessed


class FakeIndexProvider:
    def __init__(self, files):
        self.files = files

    def index_closes(self, d):
        if d not in self.files:
            raise DataNotPublished(str(d))
        return self._frame(d)

    def _frame(self, d):
        from app.market_data.base import BarFrame, Lineage, utcnow
        df = n.parse_index_close(self.files[d], d)
        df["symbol"] = df["symbol"].replace({"NIFTY 50": "TEST N50"})
        return BarFrame(df, [Lineage("nse_archive", "exchange_official", "index_close", "test", utcnow(), d, "x")])


def test_official_values_win_and_yahoo_fills_only_gaps():
    from app.database import session_scope
    from app.market_data.indices import benchmark, ensure_index, ingest_nse_indices
    from app.market_data.ingest import upsert_candles
    from app.models import MarketCandle, Stock
    d1, d2, d3 = date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 25)
    try:
        with session_scope() as db:
            r = ingest_nse_indices(db, FakeIndexProvider({d1: ind_csv(d1), d3: ind_csv(d3)}), [d1, d2, d3], names=("TEST N50",))
            assert r["bars"] == 2 and r["missing"] == [d2.isoformat()]
            sid = ensure_index(db, "TEST N50")
            yahoo = pd.DataFrame({"symbol": "TEST N50", "isin": "INDEX:TEST N50", "series": None, "session_date": [d1, d2],
                                  "ts": [n.session_bar_start(d1), n.session_bar_start(d2)], "open": [1.0, 1.0],
                                  "high": [1.0, 1.0], "low": [1.0, 1.0], "close": [99.0, 25349.65], "volume": [0, 0]})
            upsert_candles(db, yahoo, {"INDEX:TEST N50": sid}, source="yahoo", licence="unofficial", dataset_id=None, demo=False)
        with session_scope() as db:
            close, origin = benchmark(db, "TEST N50")
        assert close[d1] == 25819.25 and origin[d1] == "nse"        # official beats Yahoo's 99 on the same day
        assert close[d2] == 25349.65 and origin[d2] == "yahoo"      # official missing: Yahoo fills
        assert origin[d3] == "nse" and list(close.index) == [d1, d2, d3]
    finally:
        with session_scope() as db:
            sid = db.scalar(select(Stock.id).where(Stock.key == "INDEX:TEST N50"))
            if sid:
                db.execute(delete(MarketCandle).where(MarketCandle.stock_id == sid))
                db.execute(delete(Stock).where(Stock.id == sid))


def test_eod_waits_for_the_official_index_until_22_ist():
    from datetime import datetime
    from app.market_data.calendar import IST
    from app.scheduler.eod import wait_for_index
    today = date(2026, 11, 26)
    at = lambda h, m=0: datetime(2026, 11, 26, h, m, tzinfo=IST)   # noqa: E731
    assert wait_for_index([today], today, at(18, 20))               # file not out yet: retry at the next trigger
    assert not wait_for_index([today], today, at(22, 0))            # cut-off: score with Yahoo / carried, flagged
    assert not wait_for_index([], today, at(18, 20))                # official file present
    assert not wait_for_index([date(2026, 11, 25)], date(2026, 11, 25), at(18, 20))   # back-dated manual run


def test_scheduler_does_not_mark_the_day_done_while_waiting(monkeypatch):
    from app.scheduler import eod as pipeline
    from app.scheduler import service
    today = date(2026, 11, 26)
    monkeypatch.setattr(pipeline, "run", lambda t: {"latest_session": today, "waiting_for": "nse_index",
                                                     "waiting_sessions": [today], "new_sessions": [today]})
    captured = {}
    monkeypatch.setattr(service, "run_job", lambda job, key, fn: captured.setdefault("out", fn()))
    out = service.eod(today)
    assert out["done"] is False and "official index file" in out["reason"]


def test_account_reset_restores_capital_and_records_what_was_discarded():
    from app.database import session_scope
    from app.models import PaperAccount, TradeJournal
    from app.portfolio import service as svc
    from sqlalchemy import delete
    name = "test-reset-acct"
    try:
        with session_scope() as db:
            acct = svc.create_account(db, name, 100000.0, "nse_archive", profile="D5_runners_tighter_stops")
            db.flush()
            svc.journal(db, acct, date(2026, 11, 25), "fill", "bought something")
            acct.cash, acct.gross_bought, acct.last_processed = 90000.0, 10000.0, date(2026, 11, 25)
        with session_scope() as db:
            acct = svc.account_by_name(db, name)
            out = svc.reset_account(db, acct, date(2026, 11, 23), "benchmark corrected (ADR-018)")
            assert out["previous_last_processed"] == "2026-11-25"
        with session_scope() as db:
            acct = svc.account_by_name(db, name)
            assert (acct.cash, acct.gross_bought, acct.last_processed, acct.profile) == (
                100000.0, 0.0, date(2026, 11, 23), "D5_runners_tighter_stops")
            j = db.execute(select(TradeJournal).where(TradeJournal.account_id == acct.id)).scalars().all()
            assert [x.action for x in j] == ["account_reset"] and j[0].reason == "benchmark corrected (ADR-018)"
    finally:
        with session_scope() as db:
            db.execute(delete(PaperAccount).where(PaperAccount.name == name))
