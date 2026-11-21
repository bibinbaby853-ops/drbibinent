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
python run_scan.py --universe microcap250 --min-turnover-cr 2   # Nifty Microcap 250, lower liquidity floor
python run_scan.py --symbols RELIANCE TCS HDFCBANK    # just a few names
python run_scan.py --setups breakout "52w high"       # only those setups
python run_scan.py --include-shorts                   # also report short setups
python run_scan.py --capital 1000000 --risk-pct 0.5   # sizing inputs
python run_scan.py --min-score 70 --min-turnover-cr 20
python run_scan.py --offline                          # use the bundled symbol list
python run_scan.py --demo                             # synthetic data, no network
```

## Daily momentum report (institutional-style)

`momentum_scan.py` is a second, stricter tool built for 3-20 session
momentum swing trades. Run it after the market close:

```bash
python momentum_scan.py                                  # Nifty 500, 10 lakh capital, 0.75% risk
python momentum_scan.py --capital 2500000 --risk-pct 0.5 --max-open-risk 2
python momentum_scan.py --universe midcap150 --top 5
```

It writes an HTML report, a Markdown report and a CSV to `output/`, and
appends scores to `history/momentum_scores_<universe>.csv` so each run can show score
changes, new entries and removals against the previous session.

**Pipeline**

1. Universe and hygiene: live NSE constituents; excludes stale or suspended
   symbols, unreliable data (zero-volume runs, unexplained 40% jumps),
   turnover under 10 crore, GSM-stage stocks and trade-for-trade (BE/BZ)
   series. Narrow price bands are flagged.
2. Market first: Nifty 50 and Nifty 500 trend, breadth (above 20/50/200-day
   averages, advance/decline, new highs and lows, 10-session breadth trend)
   and a RISK-ON / NEUTRAL / RISK-OFF regime that sets risk per trade, the
   open-risk cap and the minimum score for new entries.
3. Sectors: median relative return of each NSE industry against the median
   stock, sector breadth, rotation (1-month vs 3-month rank) and RS leaders.
4. Scoring (100 points): trend 20, relative strength 20, volume 15, breakout
   quality 15, momentum indicators 10, fundamentals 10, sector 10. Missing
   inputs are N/A and the score is renormalised over the data that exists;
   each stock shows its data coverage out of 100.
5. Patterns: 20/50-day breakouts, consolidation breakouts, VCP,
   cup-and-handle, flags and pennants, breakout retests, higher lows into
   resistance and emergence from 6-month bases. Extended stocks are routed
   to the pullback list instead of being chased.
6. Enrichment of the top 40: Yahoo fundamentals and earnings dates, NSE
   delivery %, and 60-minute bars for 4-hour confirmation and 1-hour
   VWAP timing.
7. Trade plans: breakout and pullback entries, structure and ATR stops,
   targets capped by overhead resistance, R:R, holding period, invalidation
   level and position size. A portfolio plan fills READY trades within the
   open-risk cap and at most two per sector.

**Watchlists**: READY TO TRADE (score above the regime threshold, R:R at
least 1:2, no distribution bar, no failed breakout, no results within a
week), BREAKOUT WATCH and PULLBACK WATCH. Failed breakouts and removed names
are listed separately.

**Data limits**: daily data is end-of-day, intraday is delayed, and no
backtest results are included. NSE's live API blocks automated clients, so
the ASM list, promoter pledging, shareholding trends, corporate
announcements and bid-ask spreads are reported as N/A. Promoter holding is
Yahoo's insider percentage, an approximation. The report never places
orders.

## Pattern backtest

`backtest.py` replays the chart-pattern detectors day by day over the last
five years and trades every signal with the same plan the daily report prints:

```bash
python backtest.py                         # Nifty 500, about 10-20 minutes on 4 cores
python backtest.py --universe nifty100     # faster
python backtest.py --start 2022-01-01 --max-hold 15 --cost-pct 0.4
```

* No look-ahead: each day only sees data up to that day's close.
* Entries: READY setups buy the next open if it is inside the entry range;
  breakout-watch setups wait up to 5 sessions for a close above the pivot on
  1.5x volume; pullback-watch setups rest a limit order for 10 sessions.
* Exits: the plan's stop, target 1, or after 20 sessions. If a bar touches
  both the stop and the target, the stop is assumed first. Costs of 0.3%
  round trip are deducted.
* Two baselines (random days, and random uptrend days) use the same stop and
  target distances, so each pattern's hit rate can be judged against chance.

It writes `output/backtest_<universe>_<time>.html/.md/_trades.csv` and
`history/pattern_stats_<universe>.json`. The daily momentum report reads that
file and shows each setup's five-year hit rate and average R.

Limits: today's index members only, so delisted and demoted stocks are
missing and results look better than reality; only the pattern logic is
tested, not the full 100-point score; past results are not forecasts.

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
  run_scan.py            setup scanner entry point
  momentum_scan.py       daily momentum report entry point
  portfolio_review.py    read-only review of broker holdings
  backtest.py            five-year pattern backtest
  scanner/
    universe.py          NSE constituent list + bundled fallback
    data.py              yfinance download, daily cache, synthetic data
    indicators.py        SMA/EMA/RSI/ATR/ADX/MACD/Bollinger etc. in pandas
    strategies.py        setup detectors
    scoring.py           filters, composite score, trade plan
    report.py            console / CSV / HTML output
    cli.py               setup scanner orchestration
    engine.py            momentum report pipeline
    market.py            regime, breadth, sector strength and rotation
    momentum.py          100-point momentum scoring model
    patterns.py          breakout and chart pattern detectors
    tradeplan.py         entries, stops, targets, sizing, watchlists
    fundamentals.py      Yahoo fundamentals and earnings dates
    nse.py               NSE delivery data and surveillance flags
    intraday.py          4-hour confirmation and 1-hour timing
    momentum_report.py   HTML and Markdown rendering
    portfolio.py         holdings review, trailing stops, add rules
    backtest.py          walk-forward pattern backtest
  history/               daily momentum scores for change tracking
  data/nifty500_fallback.csv
  tests/                 pytest unit tests on synthetic data
```

Run the tests with `python -m pytest tests`.

## Extending

Add a detector in `scanner/strategies.py` that takes the enriched frame and
returns a `Signal`, then append it to `LONG_SETUPS` or `SHORT_SETUPS`.
New indicators go in `indicators.enrich`.
