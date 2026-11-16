#!/usr/bin/env python3
"""Daily momentum swing-trading report. Run ``python momentum_scan.py --help``."""

import argparse
import logging
from pathlib import Path

from scanner.engine import Config, run_scan
from scanner.momentum_report import write_reports
from scanner.tradeplan import BREAKOUT, PULLBACK, READY


def main() -> None:
    p = argparse.ArgumentParser(description="Institutional-style momentum scan for NSE stocks (end-of-day).")
    p.add_argument("--universe", default="nifty500", help="nifty500 (default), nifty200, midcap150, smallcap250, microcap250 or a symbol file")
    p.add_argument("--capital", type=float, default=1_000_000, help="Trading capital in INR (default 10 lakh)")
    p.add_argument("--risk-pct", type=float, default=0.75, help="Risk per trade, %% of capital (0.5-1.0)")
    p.add_argument("--max-open-risk", type=float, default=3.0, help="Maximum total open risk, %% of capital")
    p.add_argument("--min-turnover-cr", type=float, default=10.0, help="Minimum 20-day average turnover in INR crore")
    p.add_argument("--candidates", type=int, default=40, help="How many top technical names get fundamentals and intraday checks")
    p.add_argument("--top", type=int, default=10, help="Size of the ranked shortlist (5-10)")
    p.add_argument("--no-fundamentals", action="store_true")
    p.add_argument("--no-intraday", action="store_true")
    p.add_argument("--no-nse", action="store_true", help="Skip NSE delivery and surveillance files")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "output"))
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    if not 0.1 <= a.risk_pct <= 1.0:
        p.error("--risk-pct must be between 0.1 and 1.0 (the brief caps risk at 1% per trade)")
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("yfinance", "peewee", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)

    cfg = Config(universe=a.universe, capital=a.capital, risk_pct=a.risk_pct, max_open_risk_pct=a.max_open_risk,
                 min_turnover_cr=a.min_turnover_cr, candidates=a.candidates, top=a.top,
                 use_fundamentals=not a.no_fundamentals, use_intraday=not a.no_intraday, use_nse=not a.no_nse,
                 use_cache=not a.no_cache)
    res = run_scan(cfg)
    hp, mp, cp = write_reports(res, Path(a.output_dir))

    rg = res.regime
    print(f"\nData as of {res.as_of:%d %b %Y} (end of day). Market regime: {rg.label} ({rg.score:.0f}/100)")
    for n in rg.notes:
        print("  -", n)
    print(f"\nTop {len(res.top)} momentum candidates:")
    print(f"{'#':>2} {'Stock':<12}{'CMP':>10}{'Score':>7}  {'Setup':<40}{'Entry':>17}{'Stop':>10}{'T1':>10}{'T2':>10}{'R:R':>7}  List")
    for i, r in enumerate(res.top, 1):
        print(f"{i:>2} {r['Symbol']:<12}{r['CMP']:>10.2f}{r['Score']:>7.0f}  {r['Setup'][:39]:<40}"
              f"{r['EntryLow']:>8.2f}-{r['EntryHigh']:<8.2f}{r['Stop']:>10.2f}{r['T1']:>10.2f}{r['T2']:>10.2f}  1:{r['R:R']:<4.1f} {r['Watchlist'].title()}")
    for wl in (READY, BREAKOUT, PULLBACK):
        names = [f"{r['Symbol']}({r['Score']:.0f})" for r in res.rows if r["Watchlist"] == wl]
        print(f"\n{wl} ({len(names)}): " + (", ".join(names) or "none"))
    print(f"\nFailed breakouts: {', '.join(f['Symbol'] for f in res.failed) or 'none'}")
    print(f"\nReports:\n  {hp}\n  {mp}\n  {cp}")


if __name__ == "__main__":
    main()
