#!/usr/bin/env python3
"""Backtest the scanner's chart patterns over the last five years.

    python backtest.py                      # Nifty 500, last 5 years
    python backtest.py --universe nifty100  # faster
    python backtest.py --start 2022-01-01 --max-hold 15

Writes a report to output/ and per-pattern statistics to
history/pattern_stats_<universe>.json, which the daily momentum report reads
to show each setup's historical hit rate.
"""

import argparse
import html
import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.backtest import BTConfig, run_backtest, summarise
from scanner.data import download_benchmark, download_ohlcv, drop_incomplete_bars
from scanner.indicators import ema
from scanner.momentum_report import CSS
from scanner.universe import get_universe, to_yahoo_ticker

ROOT = Path(__file__).resolve().parent
E = html.escape


def table_md(df: pd.DataFrame, index_name: str) -> list[str]:
    cols = ["Trades", "Win%", "Stopped%", "TimeExit%", "AvgR", "MedianR", "AvgNet%", "ProfitFactor", "T2Reach%", "AvgDays"]
    out = [f"| {index_name} | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for k, r in df.iterrows():
        name = " / ".join(map(str, k)) if isinstance(k, tuple) else str(k)
        flag = " (small sample)" if r["Trades"] < 30 else ""
        out.append(f"| {name}{flag} | " + " | ".join(f"{r[c]:.0f}" if c == "Trades" else ("N/A" if pd.isna(r[c]) else f"{r[c]:.2f}") for c in cols) + " |")
    return out


def table_html(df: pd.DataFrame, index_name: str) -> str:
    cols = ["Trades", "Win%", "Stopped%", "TimeExit%", "AvgR", "MedianR", "AvgNet%", "ProfitFactor", "T2Reach%", "AvgDays"]
    h = ["<div class=wrap><table><tr><th>" + E(index_name) + "</th>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>"]
    for k, r in df.iterrows():
        name = " / ".join(map(str, k)) if isinstance(k, tuple) else str(k)
        good = "on" if r["AvgR"] > 0.15 and r["Trades"] >= 30 else ("off" if r["AvgR"] < 0 else "")
        h.append(f"<tr><td class='{good}'><b>{E(name)}</b>{' <span class=muted>(small sample)</span>' if r['Trades'] < 30 else ''}</td>"
                 + "".join(f"<td class=n>{r[c]:.0f}</td>" if c == "Trades" else f"<td class=n>{'N/A' if pd.isna(r[c]) else f'{r[c]:.2f}'}</td>" for c in cols) + "</tr>")
    h.append("</table></div>")
    return "".join(h)


def main() -> None:
    p = argparse.ArgumentParser(description="Five-year walk-forward backtest of the chart-pattern setups.")
    p.add_argument("--universe", default="nifty500")
    p.add_argument("--start", default="", help="First signal date, YYYY-MM-DD (default: 5 years before the latest bar)")
    p.add_argument("--max-hold", type=int, default=20)
    p.add_argument("--cost-pct", type=float, default=0.30, help="Round-trip cost in percent")
    p.add_argument("--min-turnover-cr", type=float, default=5.0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0, help="Only test the first N stocks (for a quick trial)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("yfinance", "peewee", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)

    uni = get_universe(a.universe)
    tickers = [to_yahoo_ticker(u["symbol"]) for u in uni][: a.limit or None]
    data = download_ohlcv(tickers, period="7y", cache_tag=f"bt_{Path(a.universe).stem}")
    bench = download_benchmark("^CRSLDX", period="7y")
    data, _ = drop_incomplete_bars(data)
    cfg = BTConfig(start=a.start, max_hold=a.max_hold, cost_pct=a.cost_pct, min_turnover_cr=a.min_turnover_cr)
    t0 = datetime.now()
    trades = run_backtest(data, bench, cfg, workers=a.workers)
    if trades.empty:
        print("No trades generated.")
        return
    secs = (datetime.now() - t0).total_seconds()
    pats = trades[~trades["path"].str.startswith("BASELINE")]
    base = trades[trades["path"].str.startswith("BASELINE")]
    by_pattern = summarise(pats, "pattern")
    by_path = summarise(trades, "path")
    by_stage = summarise(pats, ["pattern", "stage"])
    pats = pats.assign(year=pats["entry_date"].str[:4],
                       trend=np.where(pats["above_200"], "above 200 EMA", "below 200 EMA"),
                       rs=np.where(pats["rs_3m"] > 0, "beating Nifty 500 (3M)", "lagging Nifty 500 (3M)"),
                       vol=np.where(pats["rvol"] >= 1.5, "signal-day volume 1.5x+", "signal-day volume below 1.5x"))
    by_year = summarise(pats, "year").sort_index()
    regime_tbl = pd.DataFrame()
    if bench is not None:
        bc = bench["Close"]
        e50, e200 = ema(bc, 50), ema(bc, 200)
        reg = pd.Series("Nifty 500 weak (below 50 EMA, 50 below 200)", index=bc.index)
        reg[(bc > e50) & (e50 > e200)] = "Nifty 500 strong uptrend"
        reg[(bc > e50) ^ (e50 > e200)] = "Nifty 500 mixed"
        trades["regime"] = pd.to_datetime(trades["signal_date"]).map(reg)
        pats = pats.assign(regime=pd.to_datetime(pats["signal_date"]).map(reg))
        upb = trades[trades["path"] == "BASELINE: uptrend day"]
        regime_tbl = pd.concat([summarise(pats, "regime").rename(lambda k: f"Patterns, {k}"),
                                summarise(upb, "regime").rename(lambda k: f"Uptrend baseline, {k}")])
    by_ctx = pd.concat([summarise(pats, "trend"), summarise(pats, "rs"), summarise(pats, "vol")])
    first, last = pats["entry_date"].min(), pats["entry_date"].max()

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    stem = ROOT / "output" / f"backtest_{Path(a.universe).stem}_{stamp}"
    trades.to_csv(f"{stem}_trades.csv", index=False)

    # per-pattern stats for the daily report
    stats = {k: {"trades": int(r["Trades"]), "win_pct": float(r["Win%"]), "avg_r": float(r["AvgR"]),
                 "profit_factor": None if pd.isna(r["ProfitFactor"]) else float(r["ProfitFactor"])}
             for k, r in by_pattern.iterrows()}
    base_any = by_path.loc["BASELINE: any day"] if "BASELINE: any day" in by_path.index else None
    base_up = by_path.loc["BASELINE: uptrend day"] if "BASELINE: uptrend day" in by_path.index else None
    meta = {"universe": a.universe, "generated": datetime.now().isoformat(timespec="minutes"), "first_entry": first,
            "last_entry": last, "max_hold": a.max_hold, "cost_pct": a.cost_pct,
            "baseline_win_pct": None if base_any is None else float(base_any["Win%"]),
            "baseline_avg_r": None if base_any is None else float(base_any["AvgR"]),
            "baseline_uptrend_avg_r": None if base_up is None else float(base_up["AvgR"]), "patterns": stats}
    (ROOT / "history").mkdir(exist_ok=True)
    (ROOT / "history" / f"pattern_stats_{Path(a.universe).stem}.json").write_text(json.dumps(meta, indent=2))

    intro = [f"Universe {a.universe}: {len(data)} stocks, signals from {first} to {last}. "
             f"{len(pats):,} pattern trades and {len(base):,} baseline trades in {secs / 60:.1f} minutes.",
             f"Rules: entry as the live report prints it; exit at the plan's stop, target 1 or after {a.max_hold} sessions; "
             f"{a.cost_pct}% round-trip cost. Win% means target 1 was hit before the stop. AvgR is the average result in "
             "multiples of the initial risk, before costs; AvgNet% is after costs.",
             "How to read it: a setup is only useful if its AvgR and AvgNet% beat the baselines. The 'any day' baseline "
             "buys at random with the same stop and target distances.",
             "Limits: today's index members only (survivorship bias flatters results); Yahoo adjusted prices; the full "
             "100-point score, regime filter, delivery and fundamentals are not part of this test; past results do not "
             "guarantee future ones."]
    md = [f"# Pattern backtest: {a.universe}", *intro, "", "## By pattern", *table_md(by_pattern, "Pattern"), "",
          "## By entry path and baselines", *table_md(by_path, "Path"), "",
          "## By pattern and stage", *table_md(by_stage, "Pattern / stage"), "",
          "## By market context at the signal", *table_md(by_ctx, "Context"), "",
          "## By market regime at the signal", *table_md(regime_tbl, "Regime"), "",
          "## By year", *table_md(by_year, "Year")]
    Path(f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
    page = (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Pattern Backtest</title><style>{CSS}</style></head><body><main><h1>Pattern Backtest</h1>"
            + "".join(f"<p class=muted>{E(x)}</p>" for x in intro)
            + "<h2>By pattern</h2>" + table_html(by_pattern, "Pattern")
            + "<h2>By entry path and baselines</h2>" + table_html(by_path, "Path")
            + "<h2>By pattern and stage</h2>" + table_html(by_stage, "Pattern / stage")
            + "<h2>By market context at the signal</h2>" + table_html(by_ctx, "Context")
            + "<h2>By market regime at the signal</h2>" + table_html(regime_tbl, "Regime")
            + "<h2>By year</h2>" + table_html(by_year, "Year")
            + "<footer>Research output. Backtests are not forecasts.</footer></main></body></html>")
    Path(f"{stem}.html").write_text(page, encoding="utf-8")
    print("\n".join(md[:5]))
    print()
    print(by_pattern.to_string())
    print()
    print(by_path.to_string())
    print(f"\nReports:\n  {stem}.html\n  {stem}.md\n  {stem}_trades.csv\n  {ROOT / 'history' / ('pattern_stats_' + Path(a.universe).stem + '.json')}")


if __name__ == "__main__":
    main()
