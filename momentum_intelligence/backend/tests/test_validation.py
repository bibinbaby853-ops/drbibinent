from datetime import date

import pandas as pd

from app.market_data.validation import blocking_mask, check_series, check_session, reconcile, summarise

D = date(2026, 11, 20)


def frame(rows):
    df = pd.DataFrame(rows, columns=["symbol", "isin", "open", "high", "low", "close", "prev_close", "volume"])
    df["session_date"] = D
    df["series"] = "EQ"
    df["delivery_pct"] = 50.0
    return df


def checks(issues):
    return {(i.check, i.symbol) for i in issues}


def test_clean_session_has_no_issues():
    assert check_session(frame([["A", "INE1", 10, 11, 9, 10.5, 10, 100]])) == []


def test_session_checks_detect_each_problem():
    df = frame([
        ["DUP", "INE1", 10, 11, 9, 10, 10, 100],
        ["DUP2", "INE1", 10, 11, 9, 10, 10, 100],      # same ISIN twice
        ["BADOHLC", "INE3", 10, 9.5, 9, 10, 10, 100],   # high below open
        ["NEG", "INE4", -1, 11, 9, 10, 10, 100],
        ["NOPRICE", "INE5", None, 11, 9, 10, 10, 100],
        ["ZERO", "INE6", 10, 10, 10, 10, 10, 0],
        ["NOISIN", None, 10, 11, 9, 10, 10, 100],
        ["JUMP", "INE8", 10, 14, 10, 13.5, 10, 100],   # +35% vs published prev close
    ])
    found = checks(check_session(df))
    assert {("duplicate_key", "DUP"), ("duplicate_key", "DUP2"), ("ohlc_inconsistent", "BADOHLC"),
            ("non_positive_price", "NEG"), ("missing_price", "NOPRICE"), ("zero_volume", "ZERO"),
            ("missing_isin", "NOISIN"), ("large_move_vs_prev_close", "JUMP")} <= found
    mask = blocking_mask(df, check_session(df))
    assert set(df[mask].symbol) == {"DUP", "DUP2", "BADOHLC", "NEG", "NOPRICE"}


def test_demo_data_without_isin_is_not_flagged():
    df = frame([["DEMO01", None, 10, 11, 9, 10, 10, 100]])
    assert "missing_isin" not in {i.check for i in check_session(df, require_isin=False)}


def test_series_checks_missing_stale_and_unexplained_jumps():
    sessions = [date(2026, 11, d) for d in (16, 17, 18, 19, 20)]
    b = pd.DataFrame({"session_date": [sessions[0], sessions[1], sessions[3]], "close": [100.0, 101.0, 50.0],
                      "prev_close": [99.0, 100.0, 101.0]})
    issues = check_series(b, sessions, symbol="X", expected_last=sessions[-1])
    found = {(i.check, i.session_date) for i in issues}
    assert ("missing_session", sessions[2]) in found
    assert ("stale", sessions[3]) in found
    assert ("unexplained_jump", sessions[3]) in found
    # the same jump with a known corporate-action factor is explained
    explained = check_series(b, sessions, symbol="X", factors={sessions[3]: 0.5})
    assert "unexplained_jump" not in {i.check for i in explained}


def test_reconcile_flags_source_disagreement_only_beyond_tolerance():
    a = pd.DataFrame({"symbol": ["A", "B"], "session_date": [D, D], "close": [100.0, 200.0]})
    b = pd.DataFrame({"symbol": ["A", "B"], "session_date": [D, D], "close": [100.2, 205.0]})
    out = reconcile(a, b, primary_name="nse", secondary_name="yahoo")
    assert [i.symbol for i in out] == ["B"] and out[0].details["yahoo"] == 205.0


def test_summary_counts_by_severity():
    df = frame([["ZERO", "INE6", 10, 10, 10, 10, 10, 0]])
    assert summarise(check_session(df)) == {"warning": {"zero_volume": 1}}
