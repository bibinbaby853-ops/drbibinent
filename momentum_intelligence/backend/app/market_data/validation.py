"""Data-quality checks.

Each check returns ``Issue`` records; nothing is silently "fixed". Severity:

* ``blocking``: the bar must not be used (bad OHLC, non-positive price, duplicate key)
* ``warning``: usable but suspicious (unexplained jump, zero volume, source disagreement)
* ``info``: context (missing delivery figures, missing session for a stock)

Missing prices are reported, never filled with invented values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd

PRICE_COLS = ["open", "high", "low", "close"]
# NSE price bands are at most 20%; stocks without bands (F&O) rarely move > 25% in a day without an event.
JUMP_WARN = 0.25
PREV_CLOSE_TOLERANCE = 0.001  # 0.1% (tick rounding) before a PREV_CLOSE gap counts as an adjustment
RECONCILE_TOLERANCE = 0.005   # 0.5% close difference between two sources


@dataclass
class Issue:
    check: str
    severity: str
    symbol: str | None = None
    session_date: date | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _rows(df: pd.DataFrame, mask: pd.Series, check: str, severity: str, cols: list[str]) -> list[Issue]:
    out = []
    for _, r in df[mask].iterrows():
        details = {c: (None if pd.isna(r[c]) else (r[c].item() if hasattr(r[c], "item") else r[c])) for c in cols if c in r}
        out.append(Issue(check, severity, r.get("symbol"), r.get("session_date"), details))
    return out


def check_session(df: pd.DataFrame, *, require_isin: bool = True) -> list[Issue]:
    """Checks on one session's cross-section. ``require_isin=False`` for demo data, which has no ISIN."""
    issues: list[Issue] = []
    if df.empty:
        return [Issue("empty_session", "blocking")]
    # Both keys are checked: the same ISIN twice, or the same symbol+series twice, are each a duplicate.
    dup = (df["isin"].notna() & df.duplicated("isin", keep=False)) | df.duplicated(["symbol", "series"], keep=False)
    issues += _rows(df, dup, "duplicate_key", "blocking", ["isin", "series"])
    missing = df[PRICE_COLS].isna().any(axis=1)
    issues += _rows(df, missing, "missing_price", "blocking", PRICE_COLS)
    nonpos = (df[PRICE_COLS] <= 0).any(axis=1) & ~missing
    issues += _rows(df, nonpos, "non_positive_price", "blocking", PRICE_COLS)
    bad = ~missing & ~nonpos & ((df["low"] > df[["open", "close"]].min(axis=1)) |
                                (df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df["high"]))
    issues += _rows(df, bad, "ohlc_inconsistent", "blocking", PRICE_COLS)
    negvol = df["volume"].isna() | (df["volume"] < 0)
    issues += _rows(df, negvol, "invalid_volume", "blocking", ["volume"])
    zero = df["volume"] == 0
    issues += _rows(df, zero, "zero_volume", "warning", ["volume", "close"])
    if require_isin and df["isin"].isna().any():
        issues += _rows(df, df["isin"].isna(), "missing_isin", "warning", ["series"])
    if "prev_close" in df:
        move = (df["close"] / df["prev_close"] - 1).abs()
        issues += _rows(df, move > JUMP_WARN, "large_move_vs_prev_close", "warning", ["prev_close", "close"])
    if "delivery_pct" in df and df["delivery_pct"].isna().all():
        issues.append(Issue("delivery_missing", "info", details={"rows": int(len(df))}))
    return issues


def blocking_mask(df: pd.DataFrame, issues: list[Issue]) -> pd.Series:
    """Rows of ``df`` that have a blocking issue (matched on symbol)."""
    bad = {i.symbol for i in issues if i.severity == "blocking" and i.symbol is not None}
    return df["symbol"].isin(bad)


def check_series(bars: pd.DataFrame, sessions: list[date], *, symbol: str | None = None,
                 expected_last: date | None = None, factors: dict[date, float] | None = None) -> list[Issue]:
    """Checks on one stock's daily history (raw prices, sorted or not)."""
    issues: list[Issue] = []
    if bars.empty:
        return [Issue("no_data", "warning", symbol)]
    b = bars.sort_values("session_date").reset_index(drop=True)
    dup = b.duplicated("session_date", keep=False)
    for d in b.loc[dup, "session_date"].unique():
        issues.append(Issue("duplicate_session", "blocking", symbol, d))
    first, last = b["session_date"].min(), b["session_date"].max()
    have = set(b["session_date"])
    gaps = [s for s in sessions if first <= s <= last and s not in have]
    for s in gaps:
        issues.append(Issue("missing_session", "info", symbol, s, {"note": "no trade or suspended; not filled"}))
    if expected_last is not None and last < expected_last:
        issues.append(Issue("stale", "warning", symbol, last, {"expected_last": expected_last.isoformat()}))
    factors = factors or {}
    prev = b["close"].shift(1)
    for i in range(1, len(b)):
        d = b.at[i, "session_date"]
        move = b.at[i, "close"] / prev.iat[i] - 1
        explained = d in factors
        if abs(move) > JUMP_WARN and not explained:
            # A jump that the exchange's own PREV_CLOSE does not explain is flagged for review.
            pc = b.at[i, "prev_close"] if "prev_close" in b else np.nan
            issues.append(Issue("unexplained_jump", "warning", symbol, d,
                                {"prev_close_published": None if pd.isna(pc) else float(pc),
                                 "prior_close": float(prev.iat[i]), "close": float(b.at[i, "close"]),
                                 "move": round(float(move), 4)}))
    return issues


def reconcile(primary: pd.DataFrame, secondary: pd.DataFrame, *, tolerance: float = RECONCILE_TOLERANCE,
              primary_name: str = "primary", secondary_name: str = "secondary") -> list[Issue]:
    """Cross-source check on close prices, joined on (symbol, session_date). Both frames must be raw prices."""
    m = primary[["symbol", "session_date", "close"]].merge(
        secondary[["symbol", "session_date", "close"]], on=["symbol", "session_date"], suffixes=("_a", "_b"))
    diff = (m["close_a"] / m["close_b"] - 1).abs()
    out = []
    for _, r in m[diff > tolerance].iterrows():
        out.append(Issue("source_disagreement", "warning", r["symbol"], r["session_date"],
                         {primary_name: float(r["close_a"]), secondary_name: float(r["close_b"]),
                          "diff": round(float(r["close_a"] / r["close_b"] - 1), 5)}))
    return out


def summarise(issues: list[Issue]) -> dict:
    out: dict[str, dict[str, int]] = {}
    for i in issues:
        out.setdefault(i.severity, {}).setdefault(i.check, 0)
        out[i.severity][i.check] += 1
    return out
