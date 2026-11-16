"""End-to-end daily momentum scan: data -> filters -> market -> scoring -> plans -> history."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .data import download_benchmark, download_ohlcv
from .fundamentals import Fundamentals, fetch_fundamentals, score_fundamentals
from .indicators import enrich_momentum
from .intraday import Intraday, fetch_intraday
from .market import IndexTrend, Regime, breadth, breadth_history, index_trend, market_regime, sector_strength
from .momentum import Assessment, StockContext, assess_technical, classify, rs_composite
from .nse import delivery_stats, restriction_flags
from .patterns import failed_breakout
from .tradeplan import BREAKOUT, PULLBACK, READY, TradePlan, build_portfolio, plan_trade
from .universe import get_universe, to_yahoo_ticker

log = logging.getLogger(__name__)
HISTORY_DIR = Path(__file__).resolve().parent.parent / "history"


def history_path(universe: str) -> Path:
    """One score history per universe, so runs on different indices never overwrite each other."""
    return HISTORY_DIR / f"momentum_scores_{Path(universe).stem}.csv"


@dataclass
class Config:
    universe: str = "nifty500"
    capital: float = 1_000_000.0
    risk_pct: float = 0.75
    max_open_risk_pct: float = 3.0
    min_turnover_cr: float = 10.0
    min_price: float = 20.0
    candidates: int = 40
    top: int = 10
    use_fundamentals: bool = True
    use_intraday: bool = True
    use_nse: bool = True
    use_cache: bool = True


@dataclass
class ScanResult:
    as_of: pd.Timestamp
    generated: datetime
    cfg: Config
    n50: IndexTrend | None
    n500: IndexTrend | None
    breadth: dict
    breadth_hist: pd.Series
    regime: Regime
    sectors: pd.DataFrame
    rows: list[dict]  # every candidate with a plan, sorted by score
    top: list[dict]
    portfolio: list[dict]
    failed: list[dict]
    removed: list[dict]
    excluded: dict[str, list[str]]
    flagged: list[dict]
    data_notes: list[tuple[str, str, str]]
    universe_size: int
    scanned: int
    prev_as_of: str | None
    intraday_last: pd.Timestamp | None
    sector_leaders: dict[str, list[str]] = field(default_factory=dict)


def _integrity(df: pd.DataFrame, latest: pd.Timestamp) -> str | None:
    if df.index[-1] < latest:
        return "stale or suspended (no bar for the latest session)"
    if len(df) < 120:
        return "insufficient history (under 120 sessions)"
    last60 = df.iloc[-60:]
    if (last60["Volume"] <= 0).sum() > 3:
        return "unreliable data (repeated zero-volume sessions)"
    if (last60["Close"].pct_change().abs() > 0.40).any():
        return "unreliable data (unexplained 40%+ one-day move)"
    if last60["Close"].isna().sum() > 0:
        return "unreliable data (missing closes)"
    return None


def run_scan(cfg: Config) -> ScanResult:
    universe = get_universe(cfg.universe)
    meta = {u["symbol"]: u for u in universe}
    symbols = list(meta)
    raw = download_ohlcv([to_yahoo_ticker(s) for s in symbols], period="2y",
                         cache_tag=Path(cfg.universe).stem, use_cache=cfg.use_cache)
    bench50 = download_benchmark("^NSEI", period="2y")
    bench500 = download_benchmark("^CRSLDX", period="2y")
    if bench50 is None:
        raise RuntimeError("Nifty 50 benchmark data unavailable; cannot establish the latest session.")
    latest = bench50.index[-1]

    restrict = restriction_flags() if cfg.use_nse else pd.DataFrame()
    excluded: dict[str, list[str]] = {}
    flagged: list[dict] = []
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = raw.get(to_yahoo_ticker(sym))
        if df is None:
            excluded.setdefault("no price data", []).append(sym)
            continue
        why = _integrity(df, latest)
        if why:
            excluded.setdefault(why, []).append(sym)
            continue
        if sym in restrict.index:
            rr = restrict.loc[sym]
            if rr["GSM"]:
                excluded.setdefault("under GSM surveillance", []).append(f"{sym} ({rr['GSM']})")
                continue
            if rr["Series"] in ("BE", "BZ"):
                excluded.setdefault("trade-for-trade segment (BE/BZ series)", []).append(sym)
                continue
            if rr["PriceBand"] in ("2", "5"):
                flagged.append({"Symbol": sym, "Flag": f"{rr['PriceBand']}% price band; circuit limits can trap positions"})
        e = enrich_momentum(df)
        r = e.iloc[-1]
        if r.Close < cfg.min_price:
            excluded.setdefault(f"price under {cfg.min_price:.0f}", []).append(sym)
            continue
        if np.isnan(r.Turnover20) or r.Turnover20 / 1e7 < cfg.min_turnover_cr:
            excluded.setdefault(f"illiquid (20-day turnover under {cfg.min_turnover_cr:.0f} Cr)", []).append(sym)
            continue
        frames[sym] = e
    log.info("%d of %d stocks passed data, liquidity and restriction filters", len(frames), len(symbols))

    # ---- market context ----
    n50 = index_trend("Nifty 50", bench50)
    n500 = index_trend("Nifty 500", bench500)
    br = breadth(frames)
    brh = breadth_history(frames)
    regime = market_regime(n50, n500, br, brh, cfg.risk_pct, cfg.max_open_risk_pct)
    industry = {s: meta[s].get("industry", "") for s in frames}
    sectors = sector_strength(frames, industry, bench500 if bench500 is not None else bench50)

    # ---- RS rating across the universe ----
    comp = pd.Series({s: rs_composite(df) for s, df in frames.items()}).dropna()
    rs_rating = (comp.rank(pct=True) * 99).round(0)
    leaders = {}
    for sec in sectors.index[:8]:
        members = [s for s in rs_rating.index if industry.get(s) == sec]
        leaders[sec] = list(rs_rating[members].sort_values(ascending=False).index[:3])

    delivery = delivery_stats(list(bench50.index)) if cfg.use_nse else pd.DataFrame()

    # ---- technical assessment for everyone ----
    assessed: dict[str, Assessment] = {}
    for sym, df in frames.items():
        sec = industry.get(sym) or "Unclassified"
        ctx = StockContext(
            symbol=sym, df=df, bench50=bench50, bench500=bench500,
            rs_rating=float(rs_rating.get(sym, np.nan)), sector=sec,
            sector_row=sectors.loc[sec] if sec in sectors.index else None,
            delivery=delivery.loc[sym] if sym in delivery.index else None,
        )
        assessed[sym] = assess_technical(ctx)
    prelim = sorted(assessed.values(), key=lambda a: a.score, reverse=True)
    cands = [a.symbol for a in prelim if a.score >= 60][: cfg.candidates]
    log.info("%d candidates scored 60+ on technicals; enriching the top %d", sum(a.score >= 60 for a in prelim), len(cands))

    funds: dict[str, Fundamentals] = fetch_fundamentals(cands) if cfg.use_fundamentals else {}
    intra: dict[str, Intraday] = fetch_intraday(cands) if cfg.use_intraday else {}

    rows = []
    today = date.today()
    for sym in cands:
        a = assessed[sym]
        sec = industry.get(sym) or "Unclassified"
        fcat, fflags, events = score_fundamentals(funds.get(sym), sec, today)
        a.categories["Fundamentals"] = fcat
        a.flags.extend(fflags)
        df = frames[sym]
        plan = plan_trade(a, df, cfg.capital, regime.risk_per_trade_pct)
        score = a.score
        notes = []
        fb = failed_breakout(df)
        if fb:
            notes.append(f"Recent breakout failed: {fb}.")
            if plan.watchlist == READY:
                plan.watchlist = PULLBACK
        if plan.setup.startswith("High-volume selling"):
            last = df.iloc[-1]
            notes.append(f"Last session closed near its low on {last.RVOL:.1f}x volume, a sign of distribution.")
        if plan.rr1 < 2:
            notes.append(f"R:R to the first target is only 1:{plan.rr1:.1f} ({plan.target_note}).")
        if plan.watchlist == READY:
            if score < regime.min_score_ready:
                plan.watchlist = BREAKOUT if "breakout" in plan.setup.lower() else PULLBACK
                notes.append(f"Score below the {regime.min_score_ready:.0f} needed for new entries in a {regime.label} market.")
            elif plan.rr1 < 2:
                plan.watchlist = BREAKOUT if "breakout" in plan.setup.lower() else PULLBACK
            elif funds.get(sym) and funds[sym].earnings_date and 0 <= (funds[sym].earnings_date - today).days <= 7:
                plan.watchlist = PULLBACK
                notes.append("Results are due within a week; wait until after the announcement.")
        it = intra.get(sym)
        d = delivery.loc[sym] if sym in delivery.index else None
        fu = funds.get(sym)
        rows.append({
            "Symbol": sym, "Name": meta[sym].get("name", ""), "Sector": sec,
            "Score": score, "Class": classify(score), "Coverage": a.coverage,
            "Watchlist": plan.watchlist, "Setup": plan.setup,
            "CMP": plan.cmp, "Entry": plan.entry, "EntryLow": plan.entry_low, "EntryHigh": plan.entry_high,
            "BreakoutEntry": plan.breakout_entry, "PullbackEntry": plan.pullback_entry,
            "Stop": plan.stop, "ATRStop": plan.atr_stop, "StructureStop": plan.structure_stop,
            "T1": plan.target1, "T2": plan.target2, "R:R": plan.rr1, "R:R2": plan.rr2,
            "Risk%": plan.risk_pct, "Qty": plan.qty, "RiskAmt": plan.risk_amount,
            "Holding": plan.holding, "Invalidation": plan.invalidation, "Trigger": plan.trigger,
            "StopLogic": plan.stop_logic, "TargetNote": plan.target_note,
            "RS": a.rel, "RS500": a.rel500, "RSRating": float(rs_rating.get(sym, np.nan)), "Resilience": a.resilience,
            "Cats": a.category_scores(), "CatObjs": a.categories, "Patterns": a.patterns,
            "Flags": a.flags + notes, "Events": events, "Extended": a.extended,
            "Weekly": a.weekly_ok, "Monthly": a.monthly_ok,
            "Intraday": it, "Delivery": None if d is None else d.to_dict(), "Fund": fu,
            "SectorRow": sectors.loc[sec] if sec in sectors.index else None,
            "Daily": df.iloc[-1],
        })
    rows.sort(key=lambda x: x["Score"], reverse=True)
    rows = [r for r in rows if r["Score"] >= 65]

    # ---- top list with sector cap ----
    top, per_sec = [], {}
    for r in rows:
        if r["R:R"] < 2 or per_sec.get(r["Sector"], 0) >= 2:
            continue
        top.append(r)
        per_sec[r["Sector"]] = per_sec.get(r["Sector"], 0) + 1
        if len(top) >= cfg.top:
            break
    portfolio = build_portfolio(rows, regime.max_open_risk_pct, regime.risk_per_trade_pct)

    # ---- failed breakouts among names that were in a trend ----
    failed = []
    for sym, df in frames.items():
        msg = failed_breakout(df)
        if msg and df["EMA50"].iloc[-1] > df["EMA200"].iloc[-1]:
            failed.append({"Symbol": sym, "Sector": industry.get(sym, ""), "Detail": msg})

    # ---- history and day-over-day tracking ----
    as_of = latest.normalize()
    prev_as_of, removed = _update_history(history_path(cfg.universe), as_of, rows, assessed)
    ilast = max((it.last_bar for it in intra.values()), default=None)
    notes = _data_notes(as_of, delivery, restrict, funds, intra, ilast, cfg)
    return ScanResult(
        as_of=as_of, generated=datetime.now(), cfg=cfg, n50=n50, n500=n500, breadth=br, breadth_hist=brh,
        regime=regime, sectors=sectors, rows=rows, top=top, portfolio=portfolio, failed=failed, removed=removed,
        excluded=excluded, flagged=flagged, data_notes=notes, universe_size=len(symbols), scanned=len(frames),
        prev_as_of=prev_as_of, intraday_last=ilast, sector_leaders=leaders,
    )


def _update_history(path: Path, as_of: pd.Timestamp, rows: list[dict], assessed: dict[str, Assessment]):
    path.parent.mkdir(parents=True, exist_ok=True)
    hist = pd.read_csv(path, parse_dates=["AsOf"]) if path.exists() else pd.DataFrame(
        columns=["AsOf", "Symbol", "Score", "Watchlist", "Setup"])
    prev_dates = sorted(d for d in hist["AsOf"].unique() if pd.Timestamp(d) < as_of)
    prev = hist[hist["AsOf"] == prev_dates[-1]].set_index("Symbol") if prev_dates else None
    for r in rows:
        if prev is not None and r["Symbol"] in prev.index:
            r["Delta"] = round(r["Score"] - float(prev.loc[r["Symbol"], "Score"]), 1)
            r["Status"] = "Moved from " + prev.loc[r["Symbol"], "Watchlist"].title() if prev.loc[r["Symbol"], "Watchlist"] != r["Watchlist"] else "Unchanged list"
        else:
            r["Delta"] = None
            r["Status"] = "New" if prev is not None else "First run"
    removed = []
    if prev is not None:
        current = {r["Symbol"] for r in rows}
        for sym, pr in prev.iterrows():
            if sym not in current:
                now = assessed.get(sym)
                reason = (f"score fell to {now.score:.0f}" if now is not None else "failed data or liquidity filters")
                removed.append({"Symbol": sym, "Was": pr["Watchlist"], "PrevScore": pr["Score"], "Reason": reason})
    new = pd.DataFrame([{"AsOf": as_of, "Symbol": r["Symbol"], "Score": r["Score"], "Watchlist": r["Watchlist"],
                         "Setup": r["Setup"]} for r in rows])
    hist = pd.concat([hist[hist["AsOf"] != as_of], new], ignore_index=True)
    hist.to_csv(path, index=False, date_format="%Y-%m-%d")
    return (f"{pd.Timestamp(prev_dates[-1]):%Y-%m-%d}" if prev_dates else None), removed


def _data_notes(as_of, delivery, restrict, funds, intra, ilast, cfg) -> list[tuple[str, str, str]]:
    bd = delivery["BhavDate"].iloc[0] if len(delivery) else None
    fq = sorted({f.latest_quarter for f in funds.values() if f.latest_quarter})
    return [
        ("Daily, weekly, monthly prices", "Yahoo Finance (auto-adjusted)", f"End-of-day, session of {as_of:%d %b %Y}"),
        ("4-hour and 1-hour bars, VWAP", "Yahoo Finance 60-minute bars",
         f"Delayed; last bar {ilast:%d %b %Y %H:%M} IST" if ilast is not None else ("Disabled" if not cfg.use_intraday else "Unavailable")),
        ("Delivery volume", "NSE bhavcopy archive", f"End-of-day, {bd:%d %b %Y}" if bd is not None else "Unavailable"),
        ("GSM stage, series, price band", "NSE security list archive", "Current file" if len(restrict) else "Unavailable"),
        ("Fundamentals, earnings dates", "Yahoo Finance", f"Latest reported quarter: {', '.join(fq[-2:])}" if fq else ("Disabled" if not cfg.use_fundamentals else "Unavailable")),
        ("Promoter holding", "Yahoo 'insider' holding", "Approximation, single snapshot; trend N/A"),
        ("ASM list, promoter pledging, institutional trend", "NSE live API", "N/A: NSE blocks automated access"),
        ("Corporate announcements, news", "NSE / Yahoo news", "N/A: hosts not reachable from this scanner"),
        ("Bid-ask spread", "Order book", "N/A: liquidity filtered on turnover instead"),
        ("Backtested results", "None", "This report contains no backtest results"),
    ]
