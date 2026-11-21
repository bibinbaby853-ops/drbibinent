"""Review existing holdings with the momentum engine.

For every holding: trend health, momentum score, relative strength, volume and
delivery, fundamentals and events, a trailing stop, the money at risk to that
stop, and a plain-language signal. Account level: value, cash, P&L, open
risk, sector concentration and the market regime.

The review never places or changes orders. Holdings come from a local JSON
file (exported from the broker), which should be kept out of version control.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .data import download_benchmark, download_ohlcv, drop_incomplete_bars
from .fundamentals import fetch_fundamentals, score_fundamentals
from .indicators import enrich_momentum
from .market import breadth, breadth_history, index_trend, market_regime, sector_strength
from .momentum import StockContext, assess_technical, classify, rs_composite
from .nse import delivery_stats, fill_missing_bars, restriction_flags
from .patterns import failed_breakout, swing_points
from .tradeplan import READY, plan_trade
from .universe import NSE_INDEX_URLS, get_universe, to_yahoo_ticker

log = logging.getLogger(__name__)


@dataclass
class Holding:
    symbol: str
    qty: float
    avg_price: float
    note: str = ""
    sector: str = ""


def load_holdings(path: str | Path) -> tuple[list[Holding], float, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    hs = [Holding(h["symbol"].upper(), float(h["qty"]), float(h["avg_price"]), h.get("note", ""), h.get("sector", ""))
          for h in data["holdings"]]
    return hs, float(data.get("cash", 0.0)), data


def trailing_stop(df: pd.DataFrame) -> tuple[float, str]:
    """Chandelier exit (22-day high minus 3 ATR) or the last swing low, whichever is higher, kept at least 1 ATR away."""
    r = df.iloc[-1]
    atr = float(r.ATR14)
    chandelier = float(df["High"].iloc[-22:].max()) - 3 * atr
    lows = swing_points(df["Low"].iloc[-30:], 2, "low")
    swing = (float(df["Low"].iloc[-30:].iloc[lows[-1]]) if lows else float(df["Low"].iloc[-10:].min())) - 0.2 * atr
    stop, why = (chandelier, "22-day high minus 3 ATR") if chandelier >= swing else (swing, "below the last swing low")
    if stop > r.Close - atr:
        stop, why = float(r.Close - atr), "1 ATR below the close (both trailing levels were too tight)"
    return stop, why


def signal_for(r: pd.Series, score: float, extended: bool, failed: str | None, distribution: bool,
               below_stop: bool, pnl_pct: float) -> tuple[str, list[str]]:
    reasons = []
    trend_broken = r.Close < r.EMA50 and r.EMA20 < r.EMA50
    below_200 = not np.isnan(r.EMA200) and r.Close < r.EMA200
    if below_stop:
        reasons.append("closed below its trailing stop")
    if trend_broken:
        reasons.append("below a falling 20 and 50 EMA, so the swing trend is broken")
    if below_200:
        reasons.append("below the 200 EMA, a long-term downtrend")
    if failed:
        reasons.append(f"failed breakout ({failed})")
    if distribution:
        reasons.append("last session was a high-volume selling bar")
    if extended:
        reasons.append("extended far above its 20 EMA, so a pullback is likely")
    if below_stop or (trend_broken and (below_200 or score < 50)):
        return "EXIT SIGNAL", reasons
    if trend_broken or failed or score < 55:
        return "WEAK: REVIEW", reasons
    if distribution or score < 65:
        return "HOLD: TIGHTEN STOP", reasons
    if score >= 80 and not extended:
        return "STRONG: HOLD", reasons
    return "HOLD", reasons


def review(path: str | Path, risk_pct: float = 0.75, max_open_risk_pct: float = 3.0) -> dict:
    holdings, cash, raw_meta = load_holdings(path)
    uni = get_universe("nifty500")
    meta = {u["symbol"]: u for u in uni}
    for idx in ("microcap250", "smallcap250", "midcap150"):  # industry labels for non-Nifty-500 names
        if any(h.symbol not in meta for h in holdings):
            try:
                for u in get_universe(idx):
                    meta.setdefault(u["symbol"], u)
            except Exception:  # noqa: BLE001
                pass
    base = [to_yahoo_ticker(u["symbol"]) for u in uni]
    extra = [to_yahoo_ticker(h.symbol) for h in holdings if to_yahoo_ticker(h.symbol) not in base]
    data = download_ohlcv(base, period="2y", cache_tag="nifty500")
    if extra:
        data.update(download_ohlcv(extra, period="2y", cache_tag="portfolio_extra"))
    b50 = download_benchmark("^NSEI", period="2y")
    b500 = download_benchmark("^CRSLDX", period="2y")
    data, in_session = drop_incomplete_bars(data)
    bb, _ = drop_incomplete_bars({"a": b50, "b": b500})
    b50, b500 = bb["a"], bb["b"]
    data, filled = fill_missing_bars(data, list(b50.index))
    latest = b50.index[-1]

    frames = {}
    for t, df in data.items():
        if df is not None and len(df) >= 120 and df.index[-1] >= latest:
            frames[t[:-3]] = enrich_momentum(df)
    uni_frames = {s: f for s, f in frames.items() if s in {u["symbol"] for u in uni}}
    industry = {s: (meta.get(s) or {}).get("industry", "") or "Unclassified" for s in frames}
    n50, n500 = index_trend("Nifty 50", b50), index_trend("Nifty 500", b500)
    br, brh = breadth(uni_frames), breadth_history(uni_frames)
    regime = market_regime(n50, n500, br, brh, risk_pct, max_open_risk_pct)
    sectors = sector_strength(uni_frames, industry, b500)
    comp_uni = pd.Series({s: rs_composite(f) for s, f in uni_frames.items()}).dropna()
    delivery = delivery_stats(list(b50.index))
    if len(delivery) and delivery["BhavDate"].iloc[0] < latest.normalize():
        delivery = pd.DataFrame()
    restrict = restriction_flags()
    funds = fetch_fundamentals([h.symbol for h in holdings])

    rows = []
    for h in holdings:
        df = frames.get(h.symbol)
        if df is None:
            rows.append({"Symbol": h.symbol, "Qty": h.qty, "Avg": h.avg_price, "Signal": "NO DATA",
                         "Reasons": ["no reliable price data for the latest session"], "Note": h.note})
            continue
        r = df.iloc[-1]
        comp = rs_composite(df)
        rs_rating = float((comp_uni < comp).mean() * 99) if not np.isnan(comp) else np.nan
        sec = industry.get(h.symbol, "Unclassified")
        if sec == "Unclassified" and h.sector:
            sec = h.sector
        if sec == "Unclassified" and funds.get(h.symbol) and funds[h.symbol].yahoo_sector:
            sec = f"{funds[h.symbol].yahoo_sector} (Yahoo)"
        ctx = StockContext(h.symbol, df, b50, b500, rs_rating, sec,
                           sectors.loc[sec] if sec in sectors.index else None,
                           delivery.loc[h.symbol] if h.symbol in delivery.index else None)
        a = assess_technical(ctx)
        fcat, fflags, events = score_fundamentals(funds.get(h.symbol), sec, pd.Timestamp.today().date())
        a.categories["Fundamentals"] = fcat
        a.flags.extend(fflags)
        score = a.score
        stop, stop_why = trailing_stop(df)
        prev = df.iloc[-2]
        distribution = bool(r.Close < prev.Close and r.ClosePos <= 0.3 and r.RVOL >= 1.5)
        failed = failed_breakout(df)
        value = h.qty * r.Close
        pnl = (r.Close - h.avg_price) * h.qty
        pnl_pct = (r.Close / h.avg_price - 1) * 100
        sig, reasons = signal_for(r, score, a.extended, failed, distribution, r.Close < stop, pnl_pct)
        plan = plan_trade(a, df, 1.0, risk_pct)
        rr = restrict.loc[h.symbol] if h.symbol in restrict.index else None
        if rr is not None and (rr["GSM"] or rr["Series"] in ("BE", "BZ")):
            reasons.append(f"trading restriction: {rr['GSM'] or rr['Series'] + ' series (trade-for-trade)'}")
        fu = funds.get(h.symbol)
        d = delivery.loc[h.symbol] if h.symbol in delivery.index else None
        rows.append({
            "Symbol": h.symbol, "Name": (meta.get(h.symbol) or {}).get("name", ""), "Sector": sec, "Note": h.note,
            "Qty": h.qty, "Avg": h.avg_price, "Close": float(r.Close), "Value": value, "PnL": pnl, "PnL%": pnl_pct,
            "Chg%": float(r.Close / prev.Close - 1) * 100, "Score": score, "Class": classify(score),
            "Cats": a.category_scores(), "Signal": sig, "Reasons": reasons + a.flags, "Events": events,
            "Stop": stop, "StopWhy": stop_why, "RiskToStop": max(0.0, (r.Close - stop) * h.qty),
            "StopVsCost%": (stop / h.avg_price - 1) * 100,
            "LossAtStop": max(0.0, (h.avg_price - stop) * h.qty),
            "EMA20": float(r.EMA20), "EMA50": float(r.EMA50), "EMA200": float(r.EMA200), "RSI": float(r.RSI14),
            "ADX": float(r.ADX), "RVOL": float(r.RVOL), "RSRating": rs_rating, "RS3M": a.rel500.get("3M") if a.rel500 else None,
            "Weekly": a.weekly_ok, "Setup": plan.setup, "AddList": plan.watchlist, "AddTrigger": plan.trigger,
            "AddEntry": plan.entry, "AddLow": plan.entry_low, "AddHigh": plan.entry_high, "AddStop": plan.stop,
            "AddT1": plan.target1, "AddT2": plan.target2, "AddRR": plan.rr1, "Extended": a.extended,
            "Pattern": a.patterns[0].detail if a.patterns else "", "Deliv": None if d is None else d.get("DelivPct"),
            "Deliv20": None if d is None else d.get("DelivPct20"), "Fund": fu, "FundEvidence": fcat.evidence,
        })
    invested = sum(r.get("Value", 0) for r in rows)
    equity = invested + cash
    for r in rows:
        if "Value" in r:
            r["Weight%"] = r["Value"] / equity * 100
            r["Risk%Acct"] = r["RiskToStop"] / equity * 100
    sec_w = {}
    for r in rows:
        if "Value" in r:
            sec_w[r["Sector"]] = sec_w.get(r["Sector"], 0) + r["Weight%"]
    return {
        "as_of": latest, "in_session": in_session, "filled": filled, "regime": regime, "n50": n50, "n500": n500,
        "breadth": br, "sectors": sectors, "rows": rows, "cash": cash, "invested": invested, "equity": equity,
        "pnl": sum(r.get("PnL", 0) for r in rows), "open_risk": sum(r.get("RiskToStop", 0) for r in rows),
        "loss_at_stops": sum(r.get("LossAtStop", 0) for r in rows),
        "sector_weights": dict(sorted(sec_w.items(), key=lambda kv: -kv[1])), "source": raw_meta.get("source", ""),
        "risk_pct": risk_pct, "max_open_risk_pct": max_open_risk_pct,
    }
