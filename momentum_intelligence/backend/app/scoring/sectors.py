"""Sector rotation from equal-weighted composites of each sector's eligible constituents.

NSE sector indices are not available here and the reachable classification is
NSE's macro "industry" list, so each sector is measured from its own members:
the composite's daily return is the mean return of stocks that were eligible
the previous session (membership known before the return is earned).
"Unclassified" is never ranked (ADR-013). A sector with fewer than
``MIN_CONSTITUENTS`` members is reported but flagged ``thin`` and not ranked.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import core
from .relative_strength import bench_returns

VERSION = "1"
MIN_CONSTITUENTS = 3
RS_WEIGHTS = {21: 0.3, 63: 0.5, 126: 0.2}


def composites(close: pd.DataFrame, eligible: pd.DataFrame, sector_of: pd.Series, unclassified: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(composite levels: sessions x sector, member counts: sessions x sector)."""
    rets = close.pct_change(fill_method=None)
    members = eligible.shift(1, fill_value=False)
    levels, counts = {}, {}
    for sector, sids in sector_of.groupby(sector_of).groups.items():
        if sector == unclassified:
            continue
        cols = [s for s in sids if s in close.columns]
        m = members[cols]
        r = rets[cols].where(m)
        counts[sector] = m.sum(axis=1)
        daily = r.mean(axis=1).fillna(0.0)
        levels[sector] = (1 + daily).cumprod() * 100
    return pd.DataFrame(levels), pd.DataFrame(counts)


def compute(close: pd.DataFrame, high: pd.DataFrame, volume: pd.DataFrame, eligible: pd.DataFrame, sector_of: pd.Series,
            nifty50: pd.Series, unclassified: str = "Unclassified", change_lookback: int = 20) -> dict[str, pd.DataFrame]:
    level, count = composites(close, eligible, sector_of, unclassified)
    out: dict[str, pd.DataFrame] = {"level": level, "constituents": count}
    num = pd.DataFrame(0.0, index=level.index, columns=level.columns)
    for w, wt in RS_WEIGHTS.items():
        ex = (1 + bench_returns(level, w)).div(1 + bench_returns(nifty50, w), axis=0) - 1
        out[f"excess_{w}"] = ex
        num += ex * wt
    out["rs"] = num
    ranked = out["rs"].where(count >= MIN_CONSTITUENTS)
    out["rank"] = ranked.rank(axis=1, ascending=False, method="min")
    out["rank_change"] = out["rank"].shift(change_lookback) - out["rank"]  # positive = improved
    sma50, sma200 = level.rolling(50, min_periods=50).mean(), level.rolling(200, min_periods=200).mean()
    out["above_sma50"] = level > sma50
    out["trend_up"] = (level > sma50) & (sma50 > sma200)
    # breadth and participation among current constituents
    above50 = close > close.rolling(50, min_periods=50).mean()
    above200 = close > close.rolling(200, min_periods=200).mean()
    new_high = high >= high.rolling(252, min_periods=252).max()
    vol_exp = volume.rolling(20, min_periods=20).mean() / volume.rolling(50, min_periods=50).mean()
    b50, b200, nh, vx = {}, {}, {}, {}
    for sector in level.columns:
        cols = [s for s in sector_of.index[sector_of == sector] if s in close.columns]
        m = eligible[cols]
        n = m.sum(axis=1).replace(0, np.nan)
        b50[sector] = (above50[cols] & m).sum(axis=1) / n * 100
        b200[sector] = (above200[cols] & m).sum(axis=1) / n * 100
        nh[sector] = (new_high[cols] & m).sum(axis=1)
        vx[sector] = vol_exp[cols].where(m).median(axis=1)
    out["breadth50"], out["breadth200"] = pd.DataFrame(b50), pd.DataFrame(b200)
    out["new_highs"], out["volume_expansion"] = pd.DataFrame(nh), pd.DataFrame(vx)
    n_ranked = out["rank"].notna().sum(axis=1)
    top_half = out["rank"].le(np.ceil(n_ranked / 2), axis=0)
    out["emerging"] = (out["rank_change"] >= 3) & top_half & (out["breadth50"] > out["breadth50"].shift(change_lookback))
    return out


def rows_for(sec: dict[str, pd.DataFrame], d) -> list[dict]:
    rows = []
    for sector in sec["level"].columns:
        def g(k, cast=float):
            v = sec[k].at[d, sector] if sector in sec[k].columns else np.nan
            return None if pd.isna(v) else cast(v)
        n = g("constituents", int) or 0
        flags = []
        if n < MIN_CONSTITUENTS:
            flags.append("thin")
        if bool(sec["emerging"].at[d, sector]):
            flags.append("emerging_leadership")
        rows.append({"sector": sector, "rank": g("rank", int), "rank_change": g("rank_change", int), "constituents": n,
                     "flags": flags, "values": {
                         "rs": g("rs"), "excess_21": g("excess_21"), "excess_63": g("excess_63"), "excess_126": g("excess_126"),
                         "above_sma50": bool(sec["above_sma50"].at[d, sector]), "trend_up": bool(sec["trend_up"].at[d, sector]),
                         "breadth50_pct": g("breadth50"), "breadth200_pct": g("breadth200"), "new_52w_highs": g("new_highs", int),
                         "volume_expansion": g("volume_expansion"), "composite": g("level")}})
    return rows
