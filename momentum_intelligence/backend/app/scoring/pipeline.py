"""Run relative strength, sectors, regime and scores for a range of sessions and store them.

Indicators are computed per stock over its FULL available history (from its
first bar), so values never depend on where a run starts or how it is chunked.
Scores for history keep totals and group points; the per-check breakdown is
stored for the most recent ``detail_sessions`` sessions (and is always
recomputable).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import StrategyConfig
from ..indicators.panel import compute as compute_indicators
from ..indicators.panel import weekly_asof
from ..models import MarketRegime, MomentumScore, RelativeStrength, SectorData
from . import regime as regime_mod
from . import relative_strength as rs_mod
from . import score as score_mod
from . import sectors as sec_mod
from .panel import MarketPanel, load_panel

INPUT_FIELDS = ("close", "sma_50", "sma_200", "dist_sma_50_pct", "dist_sma_200_pct", "dist_sma_20_pct", "sma_200_slope_20",
                "sma_50_slope_10", "adx_14", "plus_di", "minus_di", "obv_slope_20", "updown_vol_ratio_20", "rel_volume_20",
                "chg_1d_pct", "dist_high_252_pct", "above_low_252_pct", "bb_width_pctrank_126", "atr_10_50_ratio", "rsi_14",
                "roc_63", "atr_pct", "vol_sma_20", "vol_sma_50", "prior_high_55", "macd", "macd_signal")
WEEKLY_FIELDS = ("w_close", "w_sma_30", "w_sma_30_slope_4")


def scoring_hash(cfg: StrategyConfig) -> str:
    from ..universe.engine import rules_of
    blob = {"universe": rules_of(cfg), "weights": cfg.score_weights.model_dump(), "bands": cfg.score_bands.model_dump(),
            "rs": cfg.relative_strength.model_dump(mode="json"), "score_version": score_mod.VERSION}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class RangeResult:
    sessions: int
    scored_rows: int
    regimes: dict


def _clean(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (np.floating, float)):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def indicator_inputs(panel: MarketPanel, sids, dates: list[date]) -> dict[int, pd.DataFrame]:
    """sid -> frame (index session_date) of scoring inputs, restricted to ``dates``."""
    out = {}
    for sid in sids:
        b = panel.bars(sid)
        if len(b) < 30:
            continue
        ind = compute_indicators(b).set_index("session_date")
        wk = weekly_asof(b).set_index("session_date")
        f = ind[list(INPUT_FIELDS)].join(wk[list(WEEKLY_FIELDS)])
        out[sid] = f.reindex(dates)
    return out


def regime_inputs(panel: MarketPanel, sec: dict, d) -> dict:
    inp: dict = {}
    for key, name in (("n50", "NIFTY 50"), ("n500", "NIFTY 500")):
        s = panel.benchmarks.get(name)
        if s is None:
            continue
        sma50, sma200 = s.rolling(50, min_periods=50).mean(), s.rolling(200, min_periods=200).mean()
        slope = (sma200 / sma200.shift(20) - 1) * 100
        for k, ser in (("close", s), ("sma50", sma50), ("sma200", sma200), ("sma200_slope", slope)):
            v = ser.get(d)
            inp[f"{key}_{k}"] = None if v is None or pd.isna(v) else float(v)
    vix = panel.benchmarks.get("INDIA VIX")
    if vix is not None and not pd.isna(vix.get(d)):
        inp["vix"] = float(vix[d])
        prev = vix.shift(10).get(d)
        inp["vix_change_10_pct"] = None if prev is None or pd.isna(prev) else float((vix[d] / prev - 1) * 100)
    return inp


def run_range(db: Session, start: date, end: date, cfg: StrategyConfig, *, detail_sessions: int = 5,
              source: str = "nse_archive", progress=None) -> RangeResult:
    panel = load_panel(db, start, end, cfg, lookback=10_000, source=source)  # full history: reproducible indicators
    dates = [d for d in panel.sessions if start <= d <= end]
    if not dates:
        return RangeResult(0, 0, {})
    if "NIFTY 50" not in panel.benchmarks:
        raise RuntimeError("Nifty 50 benchmark missing: run `python -m app.market_data.cli --live indices` first")
    elig = panel.eligible
    unclassified = cfg.universe_rules.unclassified_sector
    sec = sec_mod.compute(panel.close, panel.high, panel.volume, elig, panel.sector, panel.benchmarks["NIFTY 50"], unclassified)
    rs = rs_mod.compute(panel.close, elig, panel.benchmarks["NIFTY 50"], cfg.relative_strength,
                        nifty500=panel.benchmarks.get("NIFTY 500"), sector_close=sec["level"], sector_of=panel.sector)
    above50 = panel.close > panel.close.rolling(50, min_periods=50).mean()
    above200 = panel.close > panel.close.rolling(200, min_periods=200).mean()
    ret63 = rs_mod.window_returns(panel.close, 63)

    ever = [sid for sid in elig.columns if elig.loc[dates, sid].any()]
    inputs = indicator_inputs(panel, ever, dates)
    h = scoring_hash(cfg)
    detail_from = dates[-min(detail_sessions, len(dates))] if detail_sessions else None
    regimes: dict[str, int] = {}
    scored = 0
    # hysteresis state carries over from the stored session before the range (if any)
    prior = db.execute(select(MarketRegime).where(MarketRegime.session_date < dates[0], MarketRegime.version == regime_mod.VERSION)
                       .order_by(MarketRegime.session_date.desc()).limit(1)).scalars().first()
    state = None if prior is None else {"regime": prior.regime, "pending": prior.components.get("confirmation", {}).get("pending", [])}
    for d in dates:
        e = elig.loc[d]
        sids = [s for s in e.index[e.values] if s in inputs]
        sec_rows = sec_mod.rows_for(sec, d)
        sec_by = {r["sector"]: r for r in sec_rows}
        n_ranked = sum(1 for r in sec_rows if r["rank"] is not None)
        # regime
        ri = regime_inputs(panel, sec, d)
        n_e = int(e.sum())
        if n_e:
            ri["breadth50_pct"] = float((above50.loc[d] & e).sum() / n_e * 100)
            ri["breadth200_pct"] = float((above200.loc[d] & e).sum() / n_e * 100)
            ri["median_ret_63_pct"] = None if ret63.loc[d].where(e).notna().sum() == 0 else float(ret63.loc[d].where(e).median() * 100)
        ranked = [r for r in sec_rows if r["rank"] is not None]
        if ranked:
            ri["sector_participation_pct"] = sum(1 for r in ranked if r["values"]["above_sma50"]) / len(ranked) * 100
        reg = regime_mod.classify(ri, cfg.regime)
        conf = regime_mod.confirm(reg["regime"], state, cfg.regime)
        state = {"regime": conf["regime"], "pending": conf["pending"]}
        reg["raw_regime"], reg["regime"] = reg["regime"], conf["regime"]
        reg["confirmation"] = conf
        reg["policy"] = cfg.regime.policies[conf["regime"]].model_dump()
        regimes[reg["regime"]] = regimes.get(reg["regime"], 0) + 1

        rs_rows, score_rows = [], []
        detail = detail_from is not None and d >= detail_from
        for sid in sids:
            rec = rs_mod.row(rs, d, sid)
            rs_rows.append({"session_date": d, "stock_id": int(sid), "rs_rank": rec["rs_rank"], "rs_rank_change": rec["rs_rank_change"],
                            "classes": rec["classes"], "values": rec["values"], "version": rs_mod.VERSION})
            ind = {k: _clean(v) for k, v in inputs[sid].loc[d].items()}
            si = score_mod.derive_inputs(ind, rec, sec_by.get(panel.sector.get(sid)), n_ranked)
            sc = score_mod.score(si, cfg.score_weights, cfg.score_bands)
            score_rows.append({"session_date": d, "stock_id": int(sid), "total": sc["total"], "coverage": sc["coverage"],
                               "band": sc["band"], "groups": {k: v["points"] for k, v in sc["groups"].items()},
                               "breakdown": sc["breakdown"] if detail else None, "version": score_mod.VERSION, "config_hash": h})
        db.execute(delete(RelativeStrength).where(RelativeStrength.session_date == d, RelativeStrength.version == rs_mod.VERSION))
        db.execute(delete(SectorData).where(SectorData.session_date == d, SectorData.version == sec_mod.VERSION))
        db.execute(delete(MarketRegime).where(MarketRegime.session_date == d, MarketRegime.version == regime_mod.VERSION))
        db.execute(delete(MomentumScore).where(MomentumScore.session_date == d, MomentumScore.version == score_mod.VERSION,
                                               MomentumScore.config_hash == h))
        if rs_rows:
            db.execute(insert(RelativeStrength), rs_rows)
            db.execute(insert(MomentumScore), score_rows)
        if sec_rows:
            db.execute(insert(SectorData), [{"session_date": d, **{k: _jsonable(v) for k, v in r.items()}, "version": sec_mod.VERSION}
                                            for r in sec_rows])
        db.execute(insert(MarketRegime).values(session_date=d, regime=reg["regime"], score=reg["score"], coverage=reg["coverage"],
                                               components={"components": reg["components"], "inputs": reg["inputs"],
                                                           "raw_regime": reg["raw_regime"], "confirmation": reg["confirmation"]},
                                               policy=reg["policy"], version=regime_mod.VERSION))
        scored += len(score_rows)
        if progress:
            progress(d, reg, len(score_rows))
    db.flush()
    return RangeResult(len(dates), scored, regimes)


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    return _clean(v)
