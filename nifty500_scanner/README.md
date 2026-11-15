# Nifty 500 Swing-Trading Scanner

End-of-day scanner that downloads daily prices for every Nifty 500 stock,
computes a full set of technical indicators, and flags stocks that match
well-known swing-trading setups. Each hit gets a composite score, a
suggested entry / stop / 2R target and a position size for your account.

It is a research tool, not advice. Confirm every idea on a chart and check
news before trading.

## Quick start

```bash
cd nifty500_scanner
pip install -r requirements.txt
python run_scan.py                 # scan all Nifty 500 names
python run_scan.py --help          # every option
```

A run prints the top ideas to the console and writes a CSV plus a
sortable HTML report to `output/`.

Useful variations:

```bash
python run_scan.py --universe nifty200 --top 20       # smaller universe
python run_scan.py --symbols RELIANCE TCS HDFCBANK    # just a few names
python run_scan.py --setups breakout "52w high"       # only those setups
python run_scan.py --include-shorts                   # also report short setups
python run_scan.py --capital 1000000 --risk-pct 0.5   # sizing inputs
python run_scan.py --min-score 70 --min-turnover-cr 20
python run_scan.py --offline                          # use the bundled symbol list
python run_scan.py --demo                             # synthetic data, no network
```

## Data

* **Prices** come from Yahoo Finance through `yfinance` (NSE symbols get a
  `.NS` suffix). Two years of daily bars are fetched so the 200-day average
  and 52-week levels are available. Downloads are cached in `cache/` for
  the current day, so re-running with different filters is instant.
  Use `--no-cache` to force a fresh download.
* **Universe** is the official NSE constituent CSV. If NSE cannot be
  reached the bundled snapshot `data/nifty500_fallback.csv` is used.
  Refresh that file occasionally from
  `https://archives.nseindia.com/content/indices/ind_nifty500list.csv`.
* **Benchmark** for relative strength is Nifty 50 (`^NSEI`) by default.

## Setups detected

All setups look at the latest close only.

| Setup | Idea |
|---|---|
| Breakout 20d | Close above the prior 20-day high, volume 1.5x average, strong close, price above the 50 SMA |
| 52w High | Fresh 52-week high with volume, not yet over-bought |
| Pullback EMA20 | Uptrend (close > SMA50 > SMA200), price has dipped to the 20 EMA, RSI 35-60, ADX > 18. Flags whether the bounce is confirmed |
| Momentum Leader | 3-month gain > 10%, 6-month > 20%, ADX > 25, within 8% of the 20 EMA (not extended) |
| Oversold Dip | Above the 200 SMA but RSI(2) < 10, RSI(14) < 32 or close below the lower Bollinger band |
| Volatility Squeeze | Bollinger width in its lowest 20% of the last 120 days or ATR contraction, with an NR7 or inside bar, near highs |
| Golden Cross | 50 SMA crossed above the 200 SMA in the last 5 sessions |
| MACD Turn | MACD histogram turns positive while price holds above the 50 SMA |
| Breakdown 20d (short) | Mirror of the breakout, only with `--include-shorts` |
| Rally Into Resistance (short) | Over-bought bounce in a downtrend, only with `--include-shorts` |

## Filters and scoring

Before any setup is checked a stock must pass liquidity and sanity
filters: price above ₹20, 20-day average turnover above ₹5 crore, ATR
below 8% of price, and at least 120 bars of history. All thresholds are
flags.

The composite score blends the strongest setup (45%), trend structure
(25%), 1/3/6-month momentum (20%), a confluence bonus when several setups
fire together, and 3-month relative strength against the benchmark.

The trade plan uses the lower of the 5-day low and 2 ATR below the close
as the stop, a 2R target, and sizes the position so the stop loses
`--risk-pct` of `--capital`.

## Project layout

```
nifty500_scanner/
  run_scan.py            entry point
  scanner/
    universe.py          NSE constituent list + bundled fallback
    data.py              yfinance download, daily cache, synthetic data
    indicators.py        SMA/EMA/RSI/ATR/ADX/MACD/Bollinger etc. in pandas
    strategies.py        setup detectors
    scoring.py           filters, composite score, trade plan
    report.py            console / CSV / HTML output
    cli.py               argument parsing and orchestration
  data/nifty500_fallback.csv
  tests/                 pytest unit tests on synthetic data
```

Run the tests with `python -m pytest tests`.

## Extending

Add a detector in `scanner/strategies.py` that takes the enriched frame and
returns a `Signal`, then append it to `LONG_SETUPS` or `SHORT_SETUPS`.
New indicators go in `indicators.enrich`.
