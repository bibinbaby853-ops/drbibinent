# Momentum Intelligence: System Architecture

Version 0.1 (Phase 1). Status: proposed, awaiting owner confirmation of the
decisions in `DECISIONS.md` before Phase 2 starts.

## 1. Purpose and boundaries

An India-focused, positional (2-8 week) momentum research and **paper**
portfolio management platform for NSE equities. It scans the eligible NSE
universe, ranks stocks with a transparent deterministic score, proposes
risk-sized paper trades, manages a persistent simulated portfolio of
INR 100,000, monitors it intraday, backtests the same rules, and produces
auditable reports.

Non-goals and hard boundaries:

* **No live order placement.** The codebase contains no broker order
  endpoints. `execution_mode` is a configuration field whose only legal value
  is `paper`; any other value fails validation at start-up.
* **Not a tip generator.** Every number shown is traceable to a dataset,
  a rule and a timestamp. "No new positions" is a first-class output.
* **AI is advisory and optional.** All quantitative decisions (scores,
  signals, sizes, stops, risk checks) are deterministic Python. The AI layer
  only narrates verified data and can be switched off without loss of
  function.

## 2. Development environment (inspected 25 Nov 2026)

| Item | Finding | Consequence |
|---|---|---|
| OS / CPU / RAM / disk | Linux, 4 cores, 15 GB, 30 GB free | Enough for 5-year daily data on ~2,000 symbols |
| Python | 3.13 with pandas 3, numpy 2, pydantic 2, pytest | Backend language |
| Node.js | 22 with npm 10 | Frontend build toolchain |
| PostgreSQL | 16.15 installed, local server starts | Dev and test database without Docker |
| Redis | installed | Optional cache / job lock, not required |
| Docker | client 29.9 present, **no daemon** in this sandbox | Compose files are written and validated (`docker compose config`) but containers cannot be run here |
| NSE archives (`archives.nseindia.com`) | reachable | Official end-of-day bhavcopy, delivery, security lists, index constituents |
| NSE live site / JSON API (`www.nseindia.com`) | blocked | No live corporate-announcement or ASM feed from this host |
| Yahoo Finance (`query*.finance.yahoo.com`) | reachable, rate limited (HTTP 429 at times) | Adjusted daily history, intraday 15/60-minute bars, indices, India VIX |
| Zerodha Kite Connect (`api.kite.trade`) | **blocked by network policy** | Licensed provider adapter can be written and unit-tested but not exercised until the host is allowed and credentials exist |
| Anthropic API (`api.anthropic.com`) | reachable | Optional AI layer can run once an API key is supplied |
| Existing code | `nifty500_scanner/` (indicators, patterns, scoring, NSE archive client, walk-forward pattern backtest, 43 passing tests) | Reused and refactored into the new package, not copied blindly |

## 3. System context

```
                    +----------------------------+
 NSE archives  ---> |                            |
 (official EOD)     |   Market Data Engine       |---> data_provider_logs
 Yahoo Finance ---> |   provider interface,      |---> market_candles (raw + adjusted)
 (adjusted,         |   validation, lineage      |
  intraday, VIX)    +-------------+--------------+
 Kite Connect  --->               |
 (licensed, later)                v
                    +----------------------------+     +--------------------+
                    | Universe | Indicators | RS |---->| Scoring + Strategy |
                    | Sector rotation | Regime   |     | (deterministic)    |
                    +----------------------------+     +---------+----------+
                                                                 |
                                   +-----------------------------v-----+
                                   | Risk Engine (hard gate)            |
                                   +-----------------+------------------+
                                                     |
                     +-------------------------------v------------------+
                     | Paper Portfolio: orders, fills, positions, journal|
                     +---------+-------------------------+---------------+
                               |                         |
                 +-------------v-----+     +-------------v----------+
                 | Alerts / Intraday |     | Reports (+ optional AI)|
                 +-------------------+     +------------------------+
                               \                         /
                                +----> FastAPI REST <---+
                                          |
                                  React dashboard (mobile-first)
```

The backtester reuses the same indicator, scoring, strategy, risk and
execution modules through a simulated clock, so live paper trading and
historical simulation cannot drift apart.

## 4. Technology stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI + Uvicorn | Typed, async-capable, automatic OpenAPI docs |
| Domain logic | Python, pandas, numpy | Existing tested code, vectorised indicators |
| Persistence | PostgreSQL 16, SQLAlchemy 2.0 ORM, Alembic migrations | Auditable relational store, migrations from day one |
| Bulk history cache | Parquet files under `data/` | Fast re-computation; DB stays the system of record for decisions |
| Scheduler | APScheduler in a separate `scheduler` process, Postgres advisory locks to prevent double runs | No extra broker needed; Redis optional |
| Frontend | React 18 + TypeScript + Vite, TanStack Query, TradingView `lightweight-charts` | Responsive, works on Android Chrome; candlesticks with EMA and volume overlays |
| AI (optional) | Official `anthropic` Python SDK, default model `claude-opus-5-5`, server-side only | Narrative only, grounded on JSON snapshots |
| Tests | pytest, deterministic fixtures, Postgres integration tests | Critical-path tests gate each phase |
| Deployment | Docker Compose (db, api, scheduler, web) | One-command deployment on a VPS or home server |

## 5. Module map

| Directory | Responsibility | Phase |
|---|---|---|
| `backend/app/config.py` | Infrastructure settings (env) and validated `StrategyConfig` | 1 |
| `backend/app/main.py`, `api/` | FastAPI app, routers, request validation | 1 onward |
| `database/` | Engine, sessions, Alembic environment, advisory locks | 2 |
| `models/` | SQLAlchemy ORM models (section 9) | 2 onward |
| `market_data/` | Provider interface, NSE archive, Yahoo, Kite, Demo providers; validation; adjustment | 2 |
| `universe/` | Security master, eligibility filters, daily snapshots | 3 |
| `indicators/` | EMA/SMA/RSI/MACD/ADX/ATR/ROC/RVOL/highs/contraction, completed-candle only | 3 |
| `scoring/` | Relative strength, sector rotation, 100-point momentum score, breakdowns | 4 |
| `strategies/` | Breakout, Pullback, Continuation; signal state machine; de-duplication | 5 |
| `risk/` | Position sizing, exposure and aggregate-risk limits, correlation, gap risk, regime policy | 6 |
| `portfolio/` | Paper order book, fill simulation, costs, positions, trailing stops, journal | 6 |
| `backtesting/` | Event-driven daily simulator, walk-forward, sensitivity, metrics | 7 |
| `alerts/` | Intraday monitor on completed 15/60-minute candles, alert lifecycle | 9 |
| `scheduler/` | Job definitions, NSE session calendar, locks | 9 |
| `reports/` | Pre-market, intraday, post-market reports with sources and timestamps | 9 |
| `ai/` | Optional Claude research layer, grounding and output checks | 10 |
| `utils/` | Time (IST), money rounding, logging helpers | 1 onward |
| `frontend/` | Dashboard pages (section 11) | 8 |

## 6. Data architecture

### 6.1 Provider interface

Every provider implements one abstract interface; strategy code never imports
a concrete provider.

```python
class MarketDataProvider(Protocol):
    name: str                     # "nse_archive", "yahoo", "kite", "demo"
    licence: Literal["exchange_official", "licensed", "unofficial", "demo"]
    def daily_bars(self, symbols, start, end) -> BarFrame: ...
    def intraday_bars(self, symbols, interval, start, end) -> BarFrame: ...
    def index_bars(self, index, start, end) -> BarFrame: ...
    def security_master(self) -> list[Security]: ...
```

As built in Phase 2 (`app/market_data/base.py`): end-of-day providers also
expose `bhavcopy(date) -> BarFrame` (one session's cross-section, with
delivery figures merged in), which is what ingest uses. Corporate actions are
derived from the bars themselves (section 6.4) rather than a separate feed,
and Yahoo exposes `splits(symbol)` for confirmation only. Methods a source
cannot serve raise `ProviderUnavailable` with the reason; none return empty
data in place of an error.

Each returned dataset carries `source`, `licence`, `fetched_at` (UTC),
`as_of` (exchange timestamp of the last completed bar) and a content hash.
These fields are persisted with the rows and shown in the UI and reports.

### 6.2 Sources and labelling

| Need | Primary | Fallback | Label shown |
|---|---|---|---|
| Daily OHLCV, all NSE equities, raw | NSE bhavcopy archive (`sec_bhavdata_full_DDMMYYYY.csv`) | Yahoo raw | "NSE official end-of-day" |
| Adjusted daily history | Derived from raw bhavcopy + adjustment factors (6.4) | Yahoo adjusted series | "Adjusted by Momentum Intelligence from NSE data" |
| Delivery quantity / % | NSE bhavcopy | none (N/A) | "NSE official" |
| Intraday 15 / 60-minute | Yahoo (unofficial, delayed) | Kite Connect once allowed | "Unofficial, delayed" until a licensed feed is configured |
| Indices (Nifty 50, Nifty 500, sector indices), India VIX | Yahoo | computed equal-weight sector indices from constituents | per source |
| Security master, series, price bands, GSM | NSE `EQUITY_L.csv`, `sec_list.csv` | none | "NSE official" |
| Sector classification | NSE index constituent files (industry field) | "Unclassified" | "NSE index classification" |
| Corporate announcements | not available from this network (NSE API blocked) | none | shown as N/A, never inferred |

Yahoo and the NSE website are not licensed redistribution feeds. They are
labelled as such everywhere and the provider can be switched to a licensed
feed (Kite Connect or another vendor) through configuration only. If no real
provider is reachable the system runs in **DEMO** mode on synthetic data, with
a banner on every page and a `demo` flag on every stored row.

### 6.3 Data quality checks (run on every ingest, stored in `data_quality_issues`)

Implemented in `app/market_data/validation.py` (Phase 2):

| Check | Severity | Effect |
|---|---|---|
| Duplicate ISIN, or duplicate symbol+series, in one session | blocking | rows not stored |
| Missing / non-positive price, OHLC inconsistency, invalid volume | blocking | rows not stored |
| Missing ISIN (exchange data) | blocking | rows not stored (cannot be keyed) |
| Close moves > 25% versus the published previous close | warning | stored, flagged |
| Zero volume | warning | stored, flagged |
| Missing sessions in a stock's history | info | reported, never filled |
| Stale history (last bar before the latest session) | warning | reported |
| Unexplained jump (> 25%, no corporate-action factor) | warning | reported |
| NSE versus Yahoo close beyond 0.5% | warning | reported (`reconcile`) |
| File for a different session than requested (holiday placeholder) | - | file rejected and evicted from cache |
| Unreadable file (an Excel workbook under a .csv name is read as Excel) | - | delivery file: skipped with a note; bhavcopy: session reported as failed |
| Corporate action inferred from a price gap / disagreement with Yahoo | warning | reported; disagreement stops the factor being applied |

Incomplete bars are never stored: a daily bar only after 15:30 IST, an
intraday bar only after its end time (`calendar.py`). **Missing prices are
never fabricated.**

### 6.4 Raw versus adjusted prices and corporate actions

`market_candles` stores raw exchange prices (`is_adjusted=false`). Adjusted
rows (`is_adjusted=true`) are derived in SQL from raw rows and the factors in
`corporate_actions`: adjusted price on day d = raw x product of factors with
ex-date > d; volume divided by the same product. Raw rows are never changed.

Factors come from two rules (`adjustments.py`):

1. **Exchange-adjusted previous close** (confidence high). When NSE's
   `PREV_CLOSE(t)` differs from `CLOSE(t-1)` beyond one tick, the ratio is the
   factor, snapped to the exact fraction when tick rounding explains the
   difference.
2. **Ratio-shaped gap** (confidence medium). Phase 2 found that in 2026 NSE
   did **not** adjust the published `PREV_CLOSE` for any split or bonus found
   (e.g. BNAGROCHEM 1:10 on 16 Nov 2026: previous close 162.66, open 17.00).
   So this is the main rule in practice: a raw move beyond 35% that a common
   split/bonus ratio brings back within +-10% at the open and +-20% at the
   close is recorded as an inferred factor and flagged. Unusual ratios must
   fit within 5%. An intraday crash (open near the prior close, close far
   below) does not qualify.

Across a trading gap of more than 5 days (suspension, weekly call auction)
a published `PREV_CLOSE` can be a stale base price, so only a clean split
ratio is trusted there (medium); other gaps are stored with low confidence and
not applied.

`adjust --confirm-yahoo` checks inferred factors against Yahoo split history
(unofficial): a match raises confidence to high; a different ratio that also
explains the price gap replaces the inferred one (high); a ratio that does not
explain the gap (Yahoo often lists only part of a combined split + bonus)
leaves the inferred factor in place with a warning. Confirmation is a second
opinion, not a substitute for the exchange record.

**Market-wide guard.** If 25+ stocks (or 5% of a session) show a factor on
the same date, the previous session is missing from the database and no
factors are created; a blocking quality issue is raised instead. This caught
the two 2026 weekend sessions (Budget day 1 Feb, Muhurat trading 8 Nov),
which is why ingest now probes weekends as well.

**Results on 2026 data (221 sessions, 1 Jan - 23 Nov):** 82 factors; 63
confirmed by Yahoo, 2 corrected to Yahoo's ratio, 13 applied but
unconfirmed or disputed (flagged; some are probably demergers such as VEDL on
30 Apr, where a ratio is only an approximation), 4 stale-price gaps not
applied. Rights entitlements (`-RE`, `-RE1` symbols in the BE series) are
excluded from the equity universe.

**Identity.** `stocks.key` is the current ISIN (`DEMO:<symbol>` for demo
data). A symbol change keeps the row (ISIN unchanged). A split that changes
the ISIN keeps the row too: an unknown ISIN whose symbol traded on the
previous session under an ISIN missing today is the same company, as is one
named by `symbolchange.csv` for that day. Every symbol/ISIN a stock has had is
in `stock_symbol_history`, so back-filling older sessions maps old ISINs to
the same row.

### 6.5 Survivorship-bias control and the point-in-time universe

Each historical NSE bhavcopy lists every security that traded that day,
including stocks later delisted or renamed. The universe engine
(`app/universe/engine.py`, Phase 3) builds a snapshot for any session d from
data available at d only, one row per stock that traded on d:

| Rule | Default | Reason code |
|---|---|---|
| Instrument | company shares only: ISIN `INE...`; ETFs (`INF...`) also trade in EQ and are stored as `etf` | `not_equity` |
| Series | EQ only (BE trade-to-trade and BZ excluded) | `series` |
| Liquidity | median daily traded value over the last 20 exchange sessions >= Rs 5 Cr; untraded sessions count as zero | `illiquid` |
| History | >= 100 traded sessions | `insufficient_history` |
| Data quality | no blocking issue for the stock in the last 20 sessions | `data_quality` |
| Market data | no market-wide data gap in the last 20 sessions | `market_data_gap` |
| Corporate actions | no inferred-unconfirmed or disputed factor in the last 252 sessions (ADR-011) | `unconfirmed_corporate_action` |
| Surveillance | not in GSM, from the latest flag file on or before d (ADR-015) | `surveillance_gsm` |

All thresholds are configurable (`StrategyConfig.universe_rules`,
`min_median_traded_value_cr`, `liquidity_lookback_sessions`). Rows are keyed
by a hash of the rules: rebuilding a day with the same rules gives the same
rows even after later data arrives (tested), and a different rule set is
stored alongside rather than over it. Sector is the stored classification or
"Unclassified" (ADR-013); classification is current-only, not point-in-time.
Backtests read the snapshot for each simulated day, never today's index
membership.

### 6.6 Storage layout

* PostgreSQL: system of record for securities, candles, indicators used in
  decisions, scores, signals, orders, positions, snapshots, alerts, reports,
  audit trail.
* `data/historical/`: raw downloaded files exactly as received (bhavcopies),
  immutable, with a manifest of hashes.
* `data/processed/`: Parquet panels for fast backtests.
* `data/cache/`: disposable HTTP caches.

## 7. Processing pipelines (all times IST)

Implemented in Phase 9 (`app/scheduler/service.py`, APScheduler, `python -m app.scheduler.service`;
a `scheduler` service in Docker Compose). Every job runs through `jobs.run_job`: a Postgres
advisory lock per job (a second process gets `locked`), idempotent per key (a success is never
repeated), and a `job_runs` row with start, end, outcome, details and any error.

| Job | Trigger | Key | Steps |
|---|---|---|---|
| Pre-market | Mon-Fri 08:30 | date | expire earlier alerts; pre-market report |
| Intraday monitor | Mon-Fri every 15 min, 09:30-15:45 | date + slot | completed 15-minute candles (Yahoo) for positions, entries queued for the open, and setups; alerts (section 8.10) |
| End of day | every day, every 20 min, 18:00-23:40 | date | `scheduler.eod`: ingest, adjustments, split confirmation, reference data, indices, universe, scores, signals per configuration in use, every paper account; then the post-market report. Records `skipped` and retries until the day's NSE session is stored; weekends included for special sessions |

Paper fills at the next open are booked by the end-of-day run of that session, at its official
open price: the same price a 09:15 job would use, without depending on an intraday feed. The
earlier plan's "post-market provisional" run on Yahoo closes is not implemented: the NSE
bhavcopy arrives the same evening and is the only source used for decisions.

### 8.10 Intraday alerts (`app/alerts/`)
Rules on completed candles only (the provider drops the candle still forming): stop breached,
stop proximity (1.5%), lagging the Nifty 50 (3 points), breakout trigger crossed, approaching a
trigger (1%), volume pace (1.5x the 20-day pace, after the first hour), Nifty 50 down 2%, India
VIX up 15%, and price discontinuity (below 65% of the previous close: a probable split pauses the
other rules for that stock). Thresholds are settings. Alert states: active, resolved (non-sticky
rules whose condition stopped), acknowledged (by the user), expired (next pre-market). One alert
per session, rule, symbol and account. The watch set puts positions and today's entries first
and never drops them for the symbol cap (150). Alerts never place or change orders.

### 8.11 Daily reports (`app/reports/daily.py`)
Pre-market and post-market reports built from stored data only: regime and policy, breadth,
universe size, sector leaders and laggards, confirmed setups, forward-test accounts (fills,
pending orders, positions, reconciliation), intraday alert counts, data-quality issues. Each
states its generation time (IST), the completed session it describes, and every source with its
licence. Regenerating a report replaces it (one per kind and session).

## 8. Domain logic

### 8.1 Indicators

Implemented in `app/indicators/` (Phase 3), version `1`:

* Trend: SMA 10/20/50/100/200, EMA 10/20/50/100/200, distance from SMA
  20/50/200, SMA-50 10-session slope, SMA-200 20-session slope, ADX 14 with
  +DI/-DI (Wilder)
* Momentum: RSI 14 (Wilder), MACD 12/26/9, ROC 5/21/63/126/252
* Volatility: ATR 14 (Wilder) and ATR %, ATR 10/50 ratio, Bollinger 20/2
  (mid, bands, width, %B), Bollinger-width percentile over 126 sessions,
  20-day realised volatility, 10-session range %
* Levels: prior 20/55-session high and low (excluding today, for breakouts),
  52-week high/low and distance from them
* Volume: volume SMA 20/50, relative volume vs the prior 20 and 50 sessions,
  OBV and its 20-session change
* Weekly (trend timeframe, completed weeks only): close, SMA 10/30, RSI 14,
  SMA-30 4-week slope

Every window is causal: a test recomputes the panel on data truncated at
several dates and requires identical values. Reference tests compare against
the published Wilder RSI example and independent loop implementations (EMA,
Wilder smoothing, ATR, ADX). Values are NaN until enough history exists.
Prices are adjusted point-in-time (ADR-014). Snapshots are stored as one JSON
document per stock and session in `technical_indicators`.

### 8.2 Relative strength (`app/scoring/relative_strength.py`)
Excess return = (1 + stock return) / (1 + benchmark return) - 1 over 21, 63,
126 and 252 sessions versus the Nifty 50 (weights 0.2 / 0.4 / 0.2 / 0.2,
renormalised over the windows that exist; at least 63 sessions required).
The combined value is ranked across that session's eligible universe:
1 = weakest, 99 = strongest. Also reported: excess versus Nifty 500 and
versus the stock's sector composite (63 sessions). Classes:

* leading: rank >= 90 and not lower than 20 sessions ago
* improving: rank up 20+ points in 20 sessions
* weakening leader: rank was >= 90 twenty sessions ago and fell 10+ points
* sector outperformer: beat both its sector composite and the Nifty 50 over 63 sessions
* resilient: positive average excess return on Nifty 50 down days over 63 sessions
  (at least 5 such days) and in the top 20% of the universe on that measure

RSI is never used as relative strength.

### 8.3 Sector rotation (`app/scoring/sectors.py`)
NSE sector indices are not reachable, so each sector is an equal-weighted
composite of its eligible constituents; a stock counts toward a day's return
only if it was eligible the previous session. Per sector: excess return vs
Nifty 50 (21/63/126, weights 0.3/0.5/0.2), rank (1 = strongest), rank change
over 20 sessions, composite above its 50-day average, trend (above SMA50 and
SMA50 > SMA200), breadth (% of constituents above their 50/200-day SMA), new
52-week highs today, volume expansion (median 20/50-day volume ratio). Flags:
`emerging_leadership` (rank up 3+ places, now in the top half, breadth
improving) and `thin` (fewer than 3 constituents: shown, not ranked).
"Unclassified" is never ranked. No claims of institutional accumulation are
made from price and volume.

### 8.4 Momentum score (`app/scoring/score.py`)
28 sub-checks in seven groups, scaled to the configured weights (trend 25,
relative strength 20, volume 15, price action 15, sector 10, momentum
indicators 10, risk/reward 5). Each check records its rule, input values,
points, maximum and status (pass / partial / fail / n/a). Missing inputs are
N/A and leave the denominator: total = 100 x earned / available, coverage =
available / 100 (e.g. an Unclassified stock has coverage 0.87). Only stocks
eligible in that session's universe are scored (the liquidity and data-quality
gate). Only scale-invariant inputs are used, so the score is identical on
point-in-time or back-adjusted prices (tested). Bands: 85+ priority, 75-84
strong, 65-74 developing, below 65 watchlist. The risk/reward group is a
proxy (ATR% and distance to the 50-day SMA) until Phase 5 strategies set real
entry, stop and target levels. Scores are research output, not
recommendations, and never bypass the risk engine.

Indicators for scoring are computed from each stock's first bar, so values do
not depend on where a run starts (tested). History keeps totals and group
points; the per-check breakdown is stored for the latest sessions and is
always recomputable.

### 8.5 Strategies (`app/strategies/`, independent rule sets on completed daily bars)

| | Breakout | Pullback | Continuation |
|---|---|---|---|
| Context | Weekly close above a non-falling 30-week SMA (completed weeks); close above 20 and 50 EMA | Weekly uptrend; close above SMA200; EMA20 > EMA50; SMA50 rising | Close > EMA20 > EMA50 > SMA200; ADX >= 20; RS rank >= 80 |
| Trigger | Close above the prior 20-session high (breakout level = prior 55-session high if cleared, else 20-session) with relative volume >= 1.5 and positive RS (63-session excess > 0, RS rank >= 60) | Low within 1% of EMA20 in the last 5 sessions; those pullback days on volume < 0.9 x 20-day average; bullish confirmation bar (close > open, close > prior high, close > EMA20) | Previous session in volatility contraction (Bollinger-width percentile <= 35 or ATR10/ATR50 <= 0.85) with a 10-session base no wider than max(8%, 3 x ATR%); close above the base high, up on the day, relative volume >= 1.3 |
| Not allowed | Close more than 1 ATR above the breakout level | Any close below EMA50 in the last 5 sessions; a distribution day (down > 1.5% on > 1.5x average volume) in the last 5 | Close more than 2.5 ATR above EMA20 |
| Entry | Next session open (paper fill; chase limit 3% in execution) | same | same |
| Initial stop | Breakout level - 1 ATR | min(5-session swing low, EMA50) - 0.25 ATR | Base low - 0.2 ATR |
| Invalidation | Close back below the breakout level | Close below the swing low | Close below the base low |
| Exits | Phase 6 exit plan: 1/3 at 2R, breakeven, chandelier trail (3 ATR from +1.5R), 8-week time stop | same | same |

Every stop is clamped to between 1 and 3 ATR below the entry. A missing input
makes a condition false: no data, no signal. Levels are computed on the
adjusted series and converted to the traded price of the decision session.

**States.** early warning (context and approach, e.g. within 3% of the
breakout level, or in the pullback zone without confirmation), potential
setup (trigger met but one confirmation missing: volume or RS), confirmed
(all conditions; the stock must be eligible that session), invalidated (a
potential setup closing below its invalidation level), expired (early
warning 10 / potential setup 5 sessions without progress). A stock carries
one active signal at a time; the strongest candidate wins, ties by priority
Breakout > Continuation > Pullback. After a confirmation the stock is in a
10-session cooldown (duplicate suppression). Confirmed signals become trade
proposals and pass the regime policy and the risk engine like any other.

Signals are derived data rebuilt from a fixed anchor (the first session with
relative strength); the rules and the state machine are causal, so a
session's signals do not change when later data arrives (tested).

**Descriptive check, Jul 2021 - Oct 2026 (in-sample, no costs, stops ignored,
20 sessions after the next open), signals that pass the regime and score
gate:** pullback 7,717 (mean +1.18%, median -0.05%, 49.7% positive), breakout
3,924 (+0.83%, -0.07%, 49.6%), continuation 130 (+0.07%, -0.46%, 47.7%). A
positive mean from a minority of large winners and no median edge: whether
the full system (stops, partial exits, trailing, costs, position limits) makes
money is the Phase 7 question, answered walk-forward rather than by tuning
these rules on the same data.

### 8.6 Market regime classifier (`app/scoring/regime.py`)
Seven components, each scored -2..+2: Nifty 50 trend (weight 2), Nifty 500
trend (1), % of the universe above its 200-day SMA (1.5) and 50-day SMA (1),
% of ranked sectors above their 50-day average (1), India VIX level and
10-session change (1), median 63-session return of the universe (1). The
weighted mean is scaled to -100..+100; missing components are dropped and
reported as coverage (below 50% coverage the regime is never bullish).
Thresholds (configurable): >= 50 strong bullish, >= 20 moderately bullish,
> -20 sideways, > -50 moderately bearish, else strong bearish. Default
policy for NEW entries:

| Regime | New entries | Max per day | Risk multiplier | Min score |
|---|---|---|---|---|
| strong bullish | yes | 3 | 1.0 | 65 |
| moderately bullish | yes | 2 | 0.75 | 75 |
| sideways | yes | 1 | 0.5 | 85 |
| moderately bearish | no | 0 | 0 | - |
| strong bearish | no | 0 | 0 | - |

**Hysteresis (risk first).** The raw classification can change daily; the
regime that drives policy is confirmed: a worse raw regime applies at once,
a better one only after 3 consecutive sessions (configurable), and then the
lowest of those sessions' raw regimes applies. Without this, 2022 produced 53
regime changes (an average run of under 5 sessions). Both the raw and the
confirmed regime are stored.

Deterioration reduces new-entry eligibility; it never force-closes positions
by itself. Index levels and VIX are Yahoo data (unofficial); breadth, sector
participation and the RS distribution come from NSE data. The Nifty 50 series
is cross-checked daily against an equal-weighted composite of the 50 most
liquid eligible NSE stocks (2021-2026: daily-return correlation 0.86; the 4
days with a gap over 3% were genuine mid-cap selloffs, e.g. 4 June 2024; the 6
missing Yahoo bars are NSE weekend special sessions).

### 8.7 Risk engine (`app/risk/engine.py`, hard gate, every entry passes it)
Ten checks in a fixed order, each stored in `risk_events` (pass, capped,
reject or skipped). A check can only reduce the quantity; the first
rejection stops evaluation.

1. data_quality: eligible in that session's universe snapshot.
2. regime: policy allows new entries, today's entry count, minimum score.
3. sizing: qty = floor(equity x 0.5% x regime multiplier / effective risk),
   effective risk = max(expected fill - stop, 1 x ATR(14)) at the expected
   fill (planned entry plus slippage); stops above the entry or more than 15%
   below it are rejected.
4. position_cap: value <= 20% of equity.
5. sector_cap: sector value after the trade <= 30% ("Unclassified" is one sector).
6. position_count: open + pending < 5; no duplicate symbol.
7. aggregate_risk: initial risk of open + pending + new <= 2.5% of equity.
8. cash_reserve: cash after the purchase and charges >= 20% of equity.
9. correlation: 60-session daily-return correlation with each open position <= 0.80.
10. gap_risk: if the 95th-percentile adverse overnight gap (250 sessions) exceeds
    the stop distance, size so that gap stays within the budget.

**At fill time the limits are applied again** with the actual fill price:
an open above the plan means more risk per share, so the quantity is cut to
keep the position within its rupee risk budget and the account within the
aggregate cap (found on real 2025 data, where a gap-up pushed aggregate
initial risk to Rs 2,613 against a Rs 2,500 cap before this was added).
Drawdown alerts at 3% and 5% from the equity peak are raised once per
crossing.

### 8.8 Paper execution and portfolio accounting (`app/portfolio/`)
Daily cycle per account (`service.run_day`), one transaction per session:

0. corporate actions with ex-date that day: quantity / factor, price levels x factor
1. pending orders fill at the session's OPEN (raw exchange price) with
   slippage of 5 / 10 / 20 bps for median traded value >= Rs 50 Cr / >= 10 Cr /
   below, rounded to the 0.05 tick. An entry is cancelled if the open is at or
   below its stop or more than 3% above the planned entry, and expires if the
   stock does not trade.
2. resting stops and partial targets act on the session's high and low. A stop
   fills at min(open, stop); if a bar touches both stop and target the stop is
   assumed first (conservative).
3. after the close: mark to market; after a partial exit (1/3 at 2R by
   default) the stop moves to breakeven; from +1.5R a chandelier trail
   (highest close since entry - 3 x ATR) raises it, never lowers it; a time
   stop after 40 sessions (8 weeks) sells at the next open.
4. pending proposals dated that session go through the risk engine; approved
   ones become entry orders for the next open. A proposal added after its
   session was processed is evaluated at once; one left pending from an
   earlier session expires and is never executed on stale data.
5. snapshot, drawdown alerts and reconciliation. Cash must equal capital -
   buys - buy charges + sells - sell charges, and realised P&L must equal
   sales - cost of shares sold - all charges on them; a mismatch aborts the day.

Charges (`costs.py`, model `in-delivery-2024-10`, configurable): STT 0.1%
both sides, NSE transaction charge 0.00297%, SEBI fee Rs 10/crore, stamp duty
0.015% on buys, GST 18% on brokerage + exchange + SEBI, DP charge Rs 15.93 per
sell, brokerage configurable (0 by default). Rates change: verify against a
contract note. A session is processed once per account; every order has a
unique idempotency key. Every state change writes a `trade_journal` row with
its reason and the data session it used. Proposals come from the CLI or API
now and from Phase 5 strategies later, through the same interface.

### 8.9 Backtesting (`app/backtesting/`)

An in-memory, event-driven replica of the paper daily cycle (section 8.8), built
from the same pure functions (risk engine, ledger, exits, charges). **Parity
is verified on real data**: Jul 2025 - Nov 2026, the backtester and the paper
engine produced the same 72 trades and the same equity curve to the paisa.

* Data: raw exchange prices for fills, adjusted series for correlation and
  gap statistics, point-in-time ATR, the point-in-time universe (delisted
  stocks included: the default run traded IDFC, MINDTREE and MANGCHEFER),
  confirmed regimes, scores, corporate actions; signals regenerated from cached
  indicator frames for each strategy variant, from the same anchor as production.
* No look-ahead: tested by truncation (results up to T identical whether the
  run stops at T or later) and by replacing all prices after T.
* Costs and slippage: the paper engine's model (tested: removing costs changes
  final equity by exactly the charges).
* Metrics (`metrics.py`, formulas in its docstring, fixture-tested): total
  return, CAGR, volatility, Sharpe (rf 0 and 6.5%), Sortino, maximum drawdown
  and duration, Calmar, trade statistics in R, profit factor, exposure,
  charges, and versus the Nifty 50: beta, alpha, correlation, information ratio.
* Walk-forward (`walkforward.py`): 12-month in-sample / 6-month out-of-sample
  windows; 8 candidates (all strategies or without Breakout; 3 or 2 ATR
  trail; default or strict regime gate) ranked by in-sample Sharpe (at least
  10 trades, default on ties); the out-of-sample periods run as one continuous
  account. Only out-of-sample results are evidence.
* Sensitivity: 16 one-at-a-time variants over the full period.
* Results are stored in `backtest_runs` with the configuration, metrics,
  equity curve and trades, and served at `/api/v1/backtests`.

**Results, Jul 2021 - Nov 2026, Rs 1 lakh (historical simulation):**

| Run | Return | Sharpe | Max DD | Trades | Expectancy |
|---|---|---|---|---|---|
| Default configuration | -8.9% | -0.34 | 13.4% | 273 | -0.10R |
| Walk-forward, out-of-sample Jul 2022 - Nov 2026 | +0.9% | 0.07 | 7.4% | 174 | 0.00R |
| Nifty 50 (price index), same full period | +61.7% | | 17.2% | | |

Sensitivity (full period): only Pullback +4.3% (Sharpe 0.25, max DD 6.3%);
without Breakout +1.6%; only Breakout -9.7%; regime gate off -1.6% (expectancy
+0.08R but max DD 20.7%); no costs or slippage -4.7%; Rs 10 lakh capital -5.1%;
risk 1% -19.8%; trailing, partial-exit and time-stop variants -4.7% to -9.3%.
Average capital deployed is about 15%: 0.5% risk per trade with 1-3 ATR stops
and at most five positions leaves most of the account in cash, so even a
positive expectancy could not keep pace with a fully invested index.
**No configuration tested shows an out-of-sample edge.**

**Redesign with a locked holdout (owner's decision after Phase 7).** Protocol
committed before any result (git `89b1562`): design period 2021-07-01 to
2025-06-30, data loaded only up to its end; holdout 2025-07-01 onward, one
logged evaluation; six pre-registered designs; selection = best design-period
Sharpe among designs positive in both halves.

| Design (design period) | Return | Sharpe | Max DD | 1st half | 2nd half |
|---|---|---|---|---|---|
| D0 baseline | -2.9% | -0.11 | 13.4% | -7.9% | -1.1% |
| D1 no Breakout, gate off | +5.0% | 0.27 | 9.2% | -3.6% | +5.9% |
| D2 no Breakout, soft gate | +5.8% | 0.35 | 7.2% | -4.4% | +5.7% |
| D3 D2 + let winners run | +5.9% | 0.35 | 7.6% | -2.8% | +2.9% |
| D4 D3 + score floor 75 | +4.6% | 0.29 | 7.2% | -2.1% | +0.4% |
| **D5 D3 + stops capped at 2 ATR (selected)** | +18.5% | 0.77 | 11.7% | +6.0% | +7.0% |

Nifty 50 over the design period: +62.7%. D5's design-period profit came from
five trades (128% of the net; -Rs 4,740 without them), almost all in 2023.

**Holdout, D5, 2025-07-01 to 2026-11-23: -10.6% (Nifty 50 -0.8%), Sharpe -1.28,
max drawdown 12.0%, 71 trades, win rate 26.8%, expectancy -0.42R.** The
redesign failed out of sample. The holdout is now used: any further strategy
change has to be judged on data that does not exist yet, i.e. forward paper
trading (ADR-016).

## 9. Database schema (PostgreSQL, Alembic-managed)

| Table | Key columns | Notes |
|---|---|---|
| `stocks` | id, key (unique: ISIN or DEMO:symbol), isin, symbol, name, series, sector, status, first_seen, last_seen, demo | Symbol/ISIN history in `stock_symbol_history` |
| `trading_sessions` | (session_date, source) | A date is a session only if the exchange file exists |
| `data_quality_issues` | source, stock_id, session_date, check, severity, details | Every failed check |
| `stock_universe_snapshots` | session_date, stock_id, config_hash, eligible, reasons, sector, close, median_value_cr, history_sessions | Point-in-time universe, keyed by rule hash |
| `surveillance_flags` | stock_id, as_of, price_band_pct, gsm_stage | From NSE sec_list.csv, accumulates daily (ADR-015) |
| `market_candles` | stock_id, interval, ts, session_date, OHLCV, turnover, trades, delivery, prev_close, source, licence, dataset_id, is_adjusted, demo | Unique (stock_id, interval, ts, is_adjusted, source) |
| `corporate_actions` | stock_id, ex_date, kind, factor, source, confidence, details | Raw-to-adjusted factors |
| `technical_indicators` | isin, session_date, name, value, params_hash | Only values used in decisions are persisted |
| `relative_strength` | session_date, stock_id, rs_rank, rs_rank_change, classes, values (excess returns) | |
| `sector_data` | session_date, sector, rank, rank_change, constituents, flags, values | |
| `market_regime` | session_date, regime, score, coverage, components, policy | |
| `momentum_scores` | session_date, stock_id, config_hash, total, coverage, band, groups, breakdown (JSONB, recent sessions) | Full sub-check breakdown |
| `trade_signals` | id, key, stock_id, strategy, state, is_open, opened, closed, confirmed, entry, stop, invalidation, trigger, reasons, config_hash | State machine history in `signal_events` |
| `paper_accounts` | id, name, mode ('paper'), initial_capital, cash, realised_pnl, charges, peak_equity, last_processed | One row per paper account |
| `trade_proposals` | id, account_id, stock_id, strategy, session_date, entry, stop, target, score, status, decision, idempotency_key | Manual now, strategies later |
| `paper_orders` | id, idempotency_key (unique), proposal_id, position_id, side, kind, qty, reference_price, status, filled_session, fill_price, slippage_bps, charges | |
| `paper_positions` | id, stock_id, qty, entry_qty, avg_price, initial_stop, stop, target, initial_risk, buy_charges, highest_close, partial_done, entry_date, closed_date | |
| `portfolio_snapshots` | ts, equity, cash, invested, unrealised, realised, drawdown, exposure (JSONB) | |
| `risk_events` | ts, check, outcome, details (JSONB), order_id | Every gate decision |
| `trade_journal` | ts, position_id, action, reason, data_as_of, user_note | Audit trail |
| `backtest_runs` | id, config (JSONB), data_version, started_at, metrics (JSONB), equity_curve_path | |
| `alerts` | id, ts, isin, kind, severity, state, candle_ts, message | Only from completed candles |
| `data_provider_logs` | id, provider, dataset, started_at, rows, status, checks (JSONB), error | Lineage and quality |
| `app_settings` | key, value (JSONB), updated_at, updated_by | Dashboard-editable configuration with history |

Decision tables are append-only; corrections are new rows referencing the
row they supersede.

## 10. API (REST, versioned under `/api/v1`)

Health and system: `GET /api/health`, `GET /api/v1/settings`,
`PUT /api/v1/settings` (validated, audited), `GET /api/v1/data/status`.
Market: `/market/regime`, `/market/breadth`, `/sectors`.
Scanner: `/scanner` (filters, paging), `/stocks/{symbol}` (score breakdown,
chart data), `/watchlists/{breakout|pullback}`, `/opportunities/top`.
Portfolio: `/portfolio`, `/portfolio/positions`, `/portfolio/orders`,
`POST /portfolio/orders` (paper only, through the risk engine),
`/portfolio/journal`, `/risk`.
Backtests: `POST /backtests`, `GET /backtests/{id}`.
Alerts and reports: `/alerts`, `/reports/{kind}/{date}`.
OpenAPI documentation is generated automatically at `/docs`.
Single-user deployment with a bearer token from the environment; the API is
not meant to be exposed publicly without a reverse proxy and TLS.

## 11. Frontend (`frontend/`, Phase 8)

React 18 + TypeScript + Vite, `lightweight-charts` 4.2 for candlesticks and
lines; no other runtime dependencies. Mobile-first (checked at 360 px: every
page loads without console errors, failed API calls or horizontal scrolling),
light and dark themes from the system setting. FastAPI serves the built
`frontend/dist` from the same origin (SPA fallback), so there is no separate
web server and no CORS. The only credential the browser can hold is the
user's own dashboard token (Settings page, session storage); broker and AI
keys never leave the server.

| Page | Data |
|---|---|
| Command Centre | regime and entry policy, confirmed setups today, forward-test accounts, evidence (holdout result), highest scores |
| Market Overview | regime score history, breadth (% above 50/200-day), Nifty 50, regime components |
| All-Stock Scanner | every snapshot stock with score, RS, RVOL, trend, setup; filters by sector, score, eligibility |
| Top 20 Opportunities | stock cards for the highest scores |
| Breakout / Pullback Watchlists | signals grouped by state (confirmed, potential setup, early warning) |
| Sector Rotation | ranks, rank change, breadth, new highs, flags; rank history chart |
| Paper Portfolio | per account: equity, cash, P&L, charges, drawdown, reconciliation, positions, pending orders; equity comparison |
| Intraday Monitoring | **not built** (Phase 9 notice, no simulated data) |
| Risk Analytics | aggregate risk, invested share, positions and sector exposure against limits; drawdown; risk-engine decision counts |
| Backtesting Lab | stored runs, metrics, equity curves, sensitivity table, redesign results, walk-forward windows, holdout |
| Trade Journal | every state change with reason and data date |
| AI Daily Report | **not built** (Phase 10 notice) |
| Settings | dashboard token; validated settings form with defaults, saved with an audit history; execution mode locked to paper |
| Stock detail | candlesticks with EMA 20/50, volume and confirmed-setup markers; full score breakdown; signal history |

Stock cards show symbol, price, data source and session, score and band, RS
rank and classes, RVOL, trend status, setup with entry, stop, invalidation,
risk % and 2R target, and the main reasons in plain words.

**Settings** (`PUT /api/v1/settings`): the change is merged into the effective
configuration and validated by the same model that guards start-up; nothing is
saved if validation fails. Each save is a new `app_settings` row with the
changed paths (from -> to) and a note. Changes apply from the next end-of-day
run; changing universe, scoring or strategy rules produces results under a new
rule hash, and earlier results stay stored.

**End-of-day pipeline** (`python -m app.scheduler.eod`): ingest, adjustments,
split confirmation, reference data, indices, universe, scores, signals for
every configuration in use, and every paper account. Resumable: each step
checks what is missing rather than assuming. Forward-test accounts `fwd-D0`,
`fwd-D2`, `fwd-D5` (brief's rules and two redesigns) started after
2026-11-23 and take their profile's confirmed signals automatically.

## 12. AI research layer (optional, Phase 10, `app/ai/`)

* Official `anthropic` Python SDK (2.14.0), called only from the backend; key from
  `MI_ANTHROPIC_API_KEY`, enabled by `MI_AI_ENABLED`; never stored in the database or
  sent to the browser.
* `claude-opus-5-5` (thinking always on for this model; depth via
  `output_config.effort = medium`), streamed with `get_final_message()`, server-side
  refusal fallback `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`).
* Input: only the stored data of the day's post-market report (JSON). The system prompt
  forbids outside knowledge, new or computed numbers, predictions, targets and advice,
  and requires stating that no out-of-sample edge was found.
* Grounding check (`grounding.py`): every number in the answer must equal a value in the
  data at the precision written (rounding allowed), a list length, or part of a date in the
  data; dates must appear in the data. Any unsupported number rejects the whole commentary.
  Limit: the check proves each number is traceable to the data, not that it is attached to
  the right label.
* Outcomes stored in `ai_narratives`: accepted, rejected (with the unsupported numbers),
  refused, truncated, error, disabled. Only accepted text is returned by the API. A
  refusal, truncation or API error never fails the end-of-day job; the deterministic
  report always stands on its own.
* Runs after each post-market report (`ai_report` job, idempotent per report) or on
  demand (`POST /api/v1/reports/{id}/narrate`, refused with 409 when disabled).
* The AI cannot place orders, change settings, size positions or alter signals.
* Key handling in this sandbox: network secrets cannot be stored for Anthropic's own
  hosts, so a live call needs `MI_ANTHROPIC_API_KEY` as an environment variable (readable by
  anyone using that environment) or in the deployment's `.env`. Verified here with a test
  client; the live call is pending a key.

## 13. Security (as built, Phase 11)

* Secrets only in environment variables (`.env` on the server, mode 600, git-ignored); `.env.example` holds
  names and placeholders. Tests scan every tracked file for key patterns and the frontend source for
  credentials.
* Production start-up (`app/security.py`) refuses: no or short API token (< 32 characters), a default or
  example database password, demo data, AI enabled without a key. Applies to the API and the scheduler.
* One bearer token guards every data endpoint (constant-time comparison). Distinct wrong tokens are
  throttled per client (10 in 5 minutes → HTTP 429); repeated stale tokens and missing tokens are not counted.
* Same-origin dashboard: no CORS headers are sent, so other sites cannot call the API from a browser.
  Strict Content-Security-Policy (`script-src 'self'`), `frame-ancestors 'none'`, `nosniff`, no referrer,
  `no-store` on API responses, 1 MB request limit, API docs disabled in production.
* HTTPS terminated by Caddy (automatic certificates, HSTS); the API listens on the internal network and
  127.0.0.1 only.
* Containers: non-root user, read-only root filesystem, all capabilities dropped, `no-new-privileges`,
  rotated logs. Python dependencies locked to exact versions (`backend/requirements.lock`) and the test suite
  run inside the built image.
* No broker order client exists; a test fails the build if one appears. Settings changes are audited.

## 14. Observability (as built)

* Logs: plain text (`time level logger message`) to stdout, collected by Docker with rotation.
* `/api/health`: database, latest NSE session, data freshness (missing weekdays; one holiday tolerated),
  last stored session per benchmark index, last success per job, failures in 24 h, and a `problems` list;
  `status` is `degraded` when any problem exists. The dashboard shows the problems in a banner.
* `job_runs` (every scheduled run with status and error), `data_provider_logs` (lineage), `risk_events` and
  `trade_journal` (decisions), `alerts`, `reports`, `ai_narratives`.
* `scripts/acceptance.py` checks the brief's acceptance criteria against a running system.

## 15. Deployment (as built)

Docker Compose services: `db` (PostgreSQL 16, named volume), `api` (FastAPI serving the API and the built
dashboard; applies migrations on start), `scheduler` (same image), optional `proxy` (Caddy, `https`
profile). Backups: `scripts/backup.sh` (custom-format `pg_dump`, checksum, retention), `scripts/restore.sh`
(checksum-verified, refuses unsafe overwrites), `scripts/restore_drill.sh` (restore into a throw-away
database and compare content fingerprints). Built, run in production mode, restored, drilled and smoke-tested
in Phase 11. Procedures: `docs/DEPLOYMENT.md`, `docs/RUNBOOK.md`; evidence: `docs/ACCEPTANCE.md`.

## 16. Testing strategy

* Unit tests with deterministic fixtures for every calculation listed in the
  brief (indicators, RS, scoring, sizing, stops, limits, sector exposure,
  execution, accounting, backtest metrics, missing/stale data, corporate
  actions).
* Reference tests: indicator outputs checked against hand-computed values.
* Integration tests against a real local PostgreSQL database.
* Each phase ends only when its critical tests pass.

## 17. Reuse of the existing scanner

`nifty500_scanner/` contributes tested logic: indicators, pattern detectors,
NSE archive client (bhavcopy, security list), incomplete-bar handling,
missing-bar gap fill and the walk-forward pattern backtest findings (for
example: 50-day breakouts and long-base breakouts outperform; VCP and
cup-and-handle did not in 2021-2026). Code is ported with tests into the new
package; the old scanner stays usable until the new one replaces it.

## 18. Known limitations and risks

* Licensed real-time data is not available in this environment; intraday is
  delayed and unofficial until a licensed provider is configured.
* Corporate announcements, ASM lists and pledging are not reachable; shown as
  N/A.
* Historical sector classification is current, not point in time.
* India VIX history depends on Yahoo availability.
* Benchmarks (Nifty 50, Nifty 500, India VIX) come from Yahoo, which can lag NSE by a session. Scoring
  carries a missing index bar forward for up to 3 sessions; this was undocumented until Phase 11 and is now
  reported by `/api/health`. On 25 Nov 2026 the carried Nifty 50 value was 1.85% below the official close.
  Switching to NSE's official index file is proposed in ADR-018 (awaiting decision).
* The login throttle and the configuration cache are per process; the deployment runs one API process.

## 19. Requirements traceability

| Brief section | Where | Phase |
|---|---|---|
| 1 User configuration | `config.StrategyConfig`, `app_settings`, Settings page | 1, 8 |
| 2 Project structure | this repository layout | 1 |
| 3 Market data engine | `market_data/` | 2 |
| 4 Universe | `universe/`, `stock_universe_snapshots` | 3 |
| 5 Indicators | `indicators/` | 3 |
| 6 Relative strength | `scoring/relative_strength.py` | 4 |
| 7 Sector rotation | `scoring/sectors.py` | 4 |
| 8 Momentum score | `scoring/momentum.py` | 4 |
| 9 Strategies | `strategies/` | 5 |
| 10 Intraday monitoring | `alerts/`, `scheduler/` | 9 |
| 11 Portfolio | `portfolio/` | 6 |
| 12 Risk | `risk/` | 6 |
| 13 Regime | `scoring/regime.py` + `risk/policy.py` | 4, 6 |
| 14 Backtesting | `backtesting/` | 7 |
| 15 AI assistant | `ai/` | 10 |
| 16 Dashboard | `frontend/` | 8 |
| 17 Reports | `reports/` | 9 |
| 18 Database | `models/`, Alembic | 2 onward |
| 19 Testing | `backend/tests/` | every phase |
| 20 Deployment | `docker-compose.yml`, `docs/DEPLOYMENT.md` | 1, 11 |
| 21 Workflow | `docs/PHASES.md` | every phase |
| 22 Acceptance | `docs/ACCEPTANCE.md`, `scripts/acceptance.py` | 11 |
