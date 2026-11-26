"""Phase 9: alert rules on completed candles, the alert state machine, job idempotency, scheduler timing."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select, text

from app.alerts import rules as R
from app.config import AlertConfig
from app.market_data.calendar import IST
from app.market_data.yahoo import tidy

A = AlertConfig()
D = date(2026, 11, 26)


def bars(closes, lows=None, vols=None, start="09:15"):
    ts = pd.date_range(f"{D} {start}", periods=len(closes), freq="15min", tz=IST)
    c = np.asarray(closes, float)
    lo = np.asarray(lows if lows is not None else c * 0.998, float)
    return pd.DataFrame({"symbol": "X", "ts": ts, "open": c, "high": c * 1.002, "low": lo, "close": c,
                         "volume": vols if vols is not None else [1000] * len(c), "session_date": D})


def yahoo_frame(closes):
    idx = pd.date_range(f"{D} 09:15", periods=len(closes), freq="15min", tz=IST)
    cols = pd.MultiIndex.from_product([["X.NS"], ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
    data = [[c, c * 1.002, c * 0.998, c, c, 1000] for c in closes]
    return pd.DataFrame(data, index=idx, columns=cols)


# ------------------------------------------------------------------ completed candles only

def test_forming_candle_never_triggers_an_alert():
    raw = yahoo_frame([99, 99.5, 100.5])   # the 09:45 bar (still forming at 09:50) is the only one above 100
    early = tidy(raw, {"X.NS": "X"}, "15m", now=datetime(2026, 11, 26, 9, 50, tzinfo=IST))
    assert len(early) == 2
    assert R.watch_rules("X", early, trigger=100, strategy="breakout", state="early_warning", prev_close=98, cfg=A)[0].rule == "approaching_trigger"
    later = tidy(raw, {"X.NS": "X"}, "15m", now=datetime(2026, 11, 26, 10, 0, tzinfo=IST))
    c = R.watch_rules("X", later, trigger=100, strategy="breakout", state="early_warning", prev_close=98, cfg=A)
    assert [x.rule for x in c] == ["breakout_trigger"] and c[0].bar_end == datetime(2026, 11, 26, 10, 0, tzinfo=IST)


# ------------------------------------------------------------------ rules

def test_position_rules():
    b = bars([100, 99, 98.4], lows=[99.5, 98.5, 98.2])
    out = {c.rule for c in R.position_rules("X", b, stop=97.0, prev_close=100, nifty_ret_pct=0.5, cfg=A)}
    assert out == {"stop_proximity"}                                       # 98.2 is 1.24% above 97
    out = {c.rule for c in R.position_rules("X", b, stop=98.3, prev_close=100, nifty_ret_pct=2.0, cfg=A)}
    assert out == {"stop_breached", "rs_lagging"}                         # low 98.2 <= stop; -1.6% vs +2.0%
    assert R.position_rules("X", b, stop=90, prev_close=100, nifty_ret_pct=0.0, cfg=A) == []


def test_volume_pace_only_after_the_minimum_time():
    assert R.volume_rule("X", bars([100] * 3, vols=[50_000] * 3), avg_daily_volume=100_000, cfg=A) is None   # 45 minutes in
    c = R.volume_rule("X", bars([100] * 6, vols=[20_000] * 6), avg_daily_volume=100_000, cfg=A)               # 90 min: 120k vs 24k
    assert c.rule == "volume_pace" and c.values["pace"] == pytest.approx(120_000 / (100_000 * 90 / 375), abs=0.01)


def test_market_rules_and_discontinuity_pause():
    n = bars([24000, 23500])
    v = bars([14, 16.5])
    rules = {c.rule for c in R.market_rules(n, 24100.0, v, 14.0, A)}
    assert rules == {"index_drop", "vix_jump"}                            # -2.49%, +17.9%
    split = R.position_rules("X", bars([50, 49], lows=[49, 48]), stop=95, prev_close=100, nifty_ret_pct=0, cfg=A)
    assert [c.rule for c in split] == ["price_discontinuity"]             # no false stop alert on a split day


# ------------------------------------------------------------------ database: state machine, jobs

@pytest.fixture()
def db_ready():
    from alembic import command
    from alembic.config import Config
    from app.database import ping, session_scope
    ok, detail = ping()
    if not ok:
        pytest.skip(detail)
    command.upgrade(Config("alembic.ini"), "head")
    with session_scope() as db:
        db.execute(text("truncate alerts, job_runs restart identity"))
    return session_scope


@pytest.mark.db
def test_alert_state_machine(db_ready):
    from app.alerts.monitor import apply, expire_before
    from app.models import Alert
    scope = db_ready
    prox = R.Candidate("stop_proximity", "X", "near stop", None, {"low": 98.2}, account="fwd-D0")
    brk = R.Candidate("breakout_trigger", "Y", "above trigger", None, {"trigger": 100})

    def state():
        with scope() as db:
            return {(a.rule, a.symbol): (a.state, a.checks) for a in db.execute(select(Alert)).scalars()}

    with scope() as db:
        assert apply(db, D, [prox, brk])["new"] == 2
    with scope() as db:
        assert apply(db, D, [prox, brk])["updated"] == 2                   # deduplicated: same rows
    assert state() == {("stop_proximity", "X"): ("active", 2), ("breakout_trigger", "Y"): ("active", 2)}
    with scope() as db:
        apply(db, D, [])                                                   # conditions gone
    assert state()[("stop_proximity", "X")][0] == "resolved" and state()[("breakout_trigger", "Y")][0] == "active"  # sticky
    with scope() as db:
        assert apply(db, D, [prox])["reactivated"] == 1
        a = db.execute(select(Alert).where(Alert.symbol == "Y")).scalars().one()
        a.state = "acknowledged"
    with scope() as db:
        apply(db, D, [brk])
    assert state()[("breakout_trigger", "Y")][0] == "acknowledged"
    with scope() as db:
        assert expire_before(db, date(2026, 11, 27)) == 1                  # the active one; acknowledged stays
    assert state()[("stop_proximity", "X")][0] == "expired"


@pytest.mark.db
def test_job_runner_idempotent_retries_and_locks(db_ready):
    from app.database import get_engine
    from app.models import JobRun
    from app.scheduler.jobs import _lock_id, run_job
    calls = []
    assert run_job("t", "k1", lambda: calls.append(1) or {"x": 1})["status"] == "success"
    assert run_job("t", "k1", lambda: calls.append(1) or {"x": 1})["status"] == "already_done" and len(calls) == 1
    assert run_job("t", "k2", lambda: {"done": False})["status"] == "skipped"
    assert run_job("t", "k2", lambda: {"done": True})["status"] == "success"           # skipped -> retried
    assert run_job("t", "k3", lambda: 1 / 0)["status"] == "failed"
    assert run_job("t", "k3", lambda: {})["status"] == "success"                       # failed -> retried
    with get_engine().connect() as other:
        other.execute(text("select pg_advisory_lock(:k)"), {"k": _lock_id("t")})
        assert run_job("t", "k4", lambda: {})["status"] == "locked"
        other.execute(text("select pg_advisory_unlock(:k)"), {"k": _lock_id("t")})
        other.commit()
    with db_ready() as db:
        failed = db.execute(select(JobRun).where(JobRun.key == "k3", JobRun.status == "failed")).scalars().one()
        assert "ZeroDivisionError" in failed.error and failed.finished_at is not None


def test_intraday_job_only_in_market_hours():
    from app.scheduler.service import build_scheduler, intraday
    assert intraday(now=datetime(2026, 11, 26, 9, 20, tzinfo=IST))["status"] == "outside_market_hours"
    assert intraday(now=datetime(2026, 11, 28, 11, 0, tzinfo=IST))["status"] == "outside_market_hours"   # Saturday
    jobs = {j.id: j for j in build_scheduler().get_jobs()}
    nxt = jobs["eod"].trigger.get_next_fire_time(None, datetime(2026, 11, 26, 12, 0, tzinfo=IST))
    assert (nxt.hour, nxt.minute) == (18, 0)
    pm = jobs["pre_market"].trigger.get_next_fire_time(None, datetime(2026, 11, 27, 9, 0, tzinfo=IST))   # Friday after 08:30
    assert pm.strftime("%a %H:%M") == "Mon 08:30"


@pytest.mark.db
def test_eod_job_retries_until_the_session_exists(db_ready, monkeypatch):
    import app.scheduler.eod as pipeline
    from app.scheduler.service import eod
    monkeypatch.setattr(pipeline, "run", lambda today: {"latest_session": date(2026, 11, 25), "new_sessions": []})
    assert eod(date(2026, 11, 26))["status"] == "skipped"
    assert eod(date(2026, 11, 26))["status"] == "skipped"        # not done: tried again at the next trigger



def test_positions_are_never_dropped_by_the_symbol_cap(monkeypatch):
    import app.alerts.monitor as M
    from app.config import StrategyConfig
    ws = {"positions": [{"symbol": "ZZPOS", "stock_id": 1, "stop": 90, "account": "a"}],
          "watch": [{"symbol": f"A{i:03d}", "stock_id": 10 + i, "strategy": "breakout", "state": "early_warning", "trigger": 100} for i in range(5)]
                   + [{"symbol": "ZZCONF", "stock_id": 99, "strategy": "breakout", "state": "confirmed", "trigger": 100}],
          "prev_close": {}, "avg_vol": {}, "nifty_prev": None, "vix_prev": None, "last_session": D}
    monkeypatch.setattr(M, "watch_set", lambda *a, **k: ws)
    seen = {}

    class P:
        def _download(self, tickers, **kw):
            seen.setdefault("t", []).append(tickers)
            return pd.DataFrame()

    cfg = StrategyConfig(alerts=AlertConfig(max_symbols=2))
    # a stub session: no alerts stored, nothing to expire
    class S:
        def execute(self, *a, **k):
            class R:
                rowcount = 0
                def scalars(self): return iter(())
            return R()
        def flush(self): pass
    out = M.run(S(), cfg, [cfg], provider=P(), now=datetime(2026, 11, 26, 11, 0, tzinfo=IST))
    assert seen["t"][0] == ["ZZPOS.NS", "ZZCONF.NS"] and out["symbols_not_watched"] == 5
