"""Relative strength (never RSI): excess return versus benchmarks, combined into a 1-99 rank.

excess_w = (1 + stock return over w sessions) / (1 + benchmark return over w sessions) - 1

Combined RS = weighted mean of excess_w over the configured windows that exist
(at least ``min_window`` required), ranked across the eligible universe of that
session: 1 = weakest, 99 = strongest.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import RelativeStrengthConfig

VERSION = "1"


def window_returns(close: pd.DataFrame, w: int) -> pd.DataFrame:
    c = close.ffill(limit=5)  # an untraded day or two does not erase a return; longer gaps stay missing
    return c / c.shift(w) - 1


def bench_returns(bench: pd.Series, w: int) -> pd.Series:
    return bench / bench.shift(w) - 1


def excess(stock_ret: pd.DataFrame, bench_ret: pd.Series) -> pd.DataFrame:
    return (1 + stock_ret).div(1 + bench_ret, axis=0) - 1


def rank_1_99(values: pd.DataFrame, mask: pd.DataFrame) -> pd.DataFrame:
    v = values.where(mask)
    pct = v.rank(axis=1, method="average", pct=True)
    n = v.notna().sum(axis=1)
    # map (0,1] percentile onto 1..99; a universe of one stock gets 50
    r = 1 + (pct.mul(n, axis=0) - 1).div((n - 1).replace(0, np.nan), axis=0) * 98
    return r.where(n > 1, 50.0).where(v.notna()).round(2)


def compute(close: pd.DataFrame, eligible: pd.DataFrame, nifty50: pd.Series, cfg: RelativeStrengthConfig, *,
            nifty500: pd.Series | None = None, sector_close: pd.DataFrame | None = None,
            sector_of: pd.Series | None = None) -> dict[str, pd.DataFrame]:
    """Returns wide frames: rank, rank_change, combined, excess_<w> (vs Nifty 50), excess500_63, excess_sector_63,
    resilience, and boolean class frames."""
    out: dict[str, pd.DataFrame] = {}
    num = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    den = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for w, weight in sorted(cfg.window_weights.items()):
        ex = excess(window_returns(close, w), bench_returns(nifty50, w))
        out[f"excess_{w}"] = ex
        num += ex.fillna(0) * weight
        den += ex.notna() * weight
    combined = (num / den.replace(0, np.nan)).where(out[f"excess_{cfg.min_window}"].notna())
    out["combined"] = combined
    rank = rank_1_99(combined, eligible)
    out["rank"] = rank
    lb = cfg.change_lookback
    out["rank_change"] = rank - rank.shift(lb)
    if nifty500 is not None:
        out["excess500_63"] = excess(window_returns(close, 63), bench_returns(nifty500, 63))
    if sector_close is not None and sector_of is not None:
        sec_ret = bench_returns(sector_close, 63)  # sectors as columns
        stock_ret = window_returns(close, 63)
        aligned = pd.DataFrame({sid: sec_ret[s] if s in sec_ret else np.nan for sid, s in sector_of.items()},
                               index=close.index).reindex(columns=close.columns)
        out["excess_sector_63"] = (1 + stock_ret) / (1 + aligned) - 1
    # resilience: mean daily excess on Nifty down days over the look-back
    d_stock = close.pct_change(fill_method=None)
    d_bench = nifty50.pct_change()
    down = (d_bench < 0).astype(float)
    daily_ex = d_stock.sub(d_bench, axis=0)
    n_down = down.rolling(cfg.resilience_lookback, min_periods=cfg.resilience_lookback).sum()
    ex_down = daily_ex.mul(down, axis=0).rolling(cfg.resilience_lookback, min_periods=cfg.resilience_lookback).sum()
    out["resilience"] = ex_down.div(n_down.replace(0, np.nan), axis=0)

    prior = rank.shift(lb)
    rc = out["rank_change"]
    out["cls_leading"] = (rank >= cfg.leading_rank) & (rc >= 0)
    out["cls_improving"] = rc >= cfg.improving_rank_change
    out["cls_weakening_leader"] = (prior >= cfg.leading_rank) & (rc <= -10)
    if "excess_sector_63" in out:
        out["cls_sector_outperformer"] = (out["excess_sector_63"] > 0) & (out["excess_63"] > 0) & eligible
    enough_down = (out["resilience"] * 0 + 1).mul(n_down.ge(cfg.resilience_min_down_days).astype(float), axis=0) > 0
    res_rank = rank_1_99(out["resilience"].where(enough_down), eligible)
    out["resilience_rank"] = res_rank
    out["cls_resilient"] = (out["resilience"] > 0) & enough_down & eligible & (res_rank >= 100 - cfg.resilient_top_pct)
    return out


CLASSES = ("leading", "improving", "weakening_leader", "sector_outperformer", "resilient")


def row(rs: dict[str, pd.DataFrame], d, sid) -> dict:
    """One stock's RS record for session d (JSON-ready)."""
    def g(k):
        f = rs.get(k)
        if f is None or sid not in f.columns:
            return None
        v = f.at[d, sid]
        return None if pd.isna(v) else float(v)
    classes = [c for c in CLASSES if f"cls_{c}" in rs and sid in rs[f"cls_{c}"].columns and bool(rs[f"cls_{c}"].at[d, sid])]
    vals = {k: (None if g(k) is None else round(g(k), 6)) for k in rs
            if k.startswith("excess") or k in ("combined", "resilience", "resilience_rank")}
    return {"rs_rank": g("rank"), "rs_rank_change": g("rank_change"), "classes": classes, "values": vals}
