"""Command-line entrypoint: ``python run_scan.py --help``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import __version__
from .data import download_benchmark, download_ohlcv, synthetic_ohlcv
from .indicators import enrich
from .report import print_console, write_csv, write_html
from .scoring import Filters, build_row, passes_filters, relative_strength
from .strategies import detect
from .universe import get_universe, to_yahoo_ticker

log = logging.getLogger("scanner")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nifty500-scanner",
        description="Scan Nifty 500 stocks for swing-trading setups (end-of-day data).",
    )
    p.add_argument("--universe", default="nifty500",
                   help="nifty50 | nifty100 | nifty200 | nifty500 | midcap150 | smallcap250 | microcap250 | path to a CSV/txt of symbols")
    p.add_argument("--symbols", nargs="*", help="Scan only these NSE symbols (e.g. RELIANCE TCS)")
    p.add_argument("--period", default="2y", help="History to download (yfinance period, default 2y)")
    p.add_argument("--benchmark", default="^NSEI", help="Benchmark for relative strength (default ^NSEI = Nifty 50)")
    p.add_argument("--include-shorts", action="store_true", help="Also report short setups")
    p.add_argument("--setups", nargs="*", help="Only keep rows that include one of these setup names")
    p.add_argument("--top", type=int, default=30, help="Rows to print to the console")
    p.add_argument("--min-score", type=float, default=0.0, help="Drop rows below this composite score")
    p.add_argument("--min-price", type=float, default=20.0)
    p.add_argument("--min-turnover-cr", type=float, default=5.0, help="Min 20-day avg traded value in INR crore")
    p.add_argument("--max-atr-pct", type=float, default=8.0)
    p.add_argument("--capital", type=float, default=500000.0, help="Account size for position sizing (INR)")
    p.add_argument("--risk-pct", type=float, default=1.0, help="Risk per trade as %% of capital")
    p.add_argument("--output-dir", default=str(Path(__file__).resolve().parent.parent / "output"))
    p.add_argument("--no-cache", action="store_true", help="Ignore today's on-disk price cache")
    p.add_argument("--offline", action="store_true", help="Never hit NSE for the constituent list (use bundled snapshot)")
    p.add_argument("--demo", action="store_true", help="Run on synthetic data (no network) to exercise the pipeline")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _demo_data(symbols: list[str]) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    data = {}
    for i, s in enumerate(symbols):
        drift = (i % 5 - 2) * 0.0008  # mix of up, flat and down trends
        data[to_yahoo_ticker(s)] = synthetic_ohlcv(seed=i, drift=drift, vol=0.015 + (i % 3) * 0.005)
    bench = synthetic_ohlcv(seed=999, drift=0.0003, vol=0.01)
    return data, bench


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # 1. Universe
    universe = get_universe(args.universe, offline=args.offline or args.demo)
    if args.symbols:
        wanted = {s.upper() for s in args.symbols}
        known = {u["symbol"]: u for u in universe}
        universe = [known.get(s, {"symbol": s, "name": "", "industry": ""}) for s in wanted]
    meta_by_symbol = {u["symbol"]: u for u in universe}
    symbols = [u["symbol"] for u in universe]
    log.info("Universe: %d symbols", len(symbols))

    # 2. Prices
    if args.demo:
        data, bench = _demo_data(symbols[:60] if not args.symbols else symbols)
    else:
        tickers = [to_yahoo_ticker(s) for s in symbols]
        data = download_ohlcv(tickers, period=args.period, cache_tag=args.universe.replace("/", "_"),
                              use_cache=not args.no_cache)
        bench = download_benchmark(args.benchmark, period=args.period) if args.benchmark else None
        if bench is not None:
            from .nse import fill_missing_bars
            data, filled = fill_missing_bars(data, list(bench.index))
            if filled:
                log.info("Filled latest bars for %d stocks from the NSE bhavcopy", len(filled))
        if bench is None:
            log.warning("Benchmark %s unavailable; relative strength will be blank", args.benchmark)

    # 3. Scan
    filters = Filters(min_price=args.min_price, min_turnover_cr=args.min_turnover_cr, max_atr_pct=args.max_atr_pct)
    rows, skipped = [], {}
    for ticker, raw in data.items():
        symbol = ticker[:-3] if ticker.endswith(".NS") else ticker
        df = enrich(raw)
        ok, why = passes_filters(df, filters)
        if not ok:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        signals = detect(df, include_shorts=args.include_shorts)
        if not signals:
            continue
        rs = relative_strength(df, bench)
        rows.append(build_row(symbol, meta_by_symbol.get(symbol, {}), df, signals, rs, args.capital, args.risk_pct))

    results = pd.DataFrame(rows)
    if not results.empty:
        if args.setups:
            wanted = [s.lower() for s in args.setups]
            results = results[results["Setups"].str.lower().apply(lambda s: any(w in s for w in wanted))]
        results = results[results["Score"] >= args.min_score]
        results = results.sort_values(["Score", "RVOL"], ascending=[False, False]).reset_index(drop=True)

    # 4. Output
    as_of = max((df.index[-1] for df in data.values()), default=pd.Timestamp.today()).strftime("%Y-%m-%d")
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path(args.output_dir)
    tag = "demo" if args.demo else Path(args.universe).stem
    csv_path = write_csv(results, out_dir / f"scan_{tag}_{stamp}.csv")
    html_path = write_html(
        results,
        out_dir / f"scan_{tag}_{stamp}.html",
        title=f"{'Demo' if args.demo else tag.upper()} setups as of {as_of}",
        meta={
            "Scanned": len(data),
            "Hits": len(results),
            "Skipped": ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())) or "none",
            "Capital": f"₹{args.capital:,.0f}",
            "Risk/trade": f"{args.risk_pct}%",
            "Shorts": "on" if args.include_shorts else "off",
        },
    )

    print(f"\nScanned {len(data)} stocks, {len(results)} with setups (data as of {as_of})")
    if skipped:
        print("Skipped: " + ", ".join(f"{v} {k}" for k, v in sorted(skipped.items())))
    print()
    print_console(results, top=args.top)
    print(f"\nCSV : {csv_path}\nHTML: {html_path}")
    return 0


def main() -> None:
    sys.exit(run())
