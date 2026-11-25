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

## 7. Processing pipelines (all times IST, NSE trading days only)

| Job | When | Steps |
|---|---|---|
| Pre-market | 08:30 | Load last completed session, regime, watchlist, portfolio risk, report |
| Intraday monitor | every 15 minutes, 09:30 to 15:30, on **completed** candles | Breakout, volume, approach-to-entry, stop proximity, RS weakening, regime change alerts |
| Post-market provisional | 16:00 | Run on provider closes (Yahoo); label provisional |
| End-of-day final | when the NSE bhavcopy appears (polled from 18:00) | Ingest, validate, adjust, universe snapshot, indicators, RS, sectors, regime, scores, signals, risk-checked paper orders for next open, portfolio snapshot, post-market report |
| Next open execution | 09:15 + first completed candle | Fill queued paper orders at next-bar open with slippage |

Each job takes a Postgres advisory lock, is idempotent (keyed by session
date), and records start, end, inputs and outcome.

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

### 8.2 Relative strength
Excess return versus Nifty 50, Nifty 500 and the stock's sector index over 1,
3, 6 and 12 months, combined into a normalised percentile rank (0-99).
Classifications: leading (top decile, rising), improving (rank up 20+ points
in 4 weeks), weakening leader (was top decile, falling), sector outperformer,
resilient (positive excess return on index down days). RSI is never used as
relative strength.

### 8.3 Sector rotation
Sector RS, momentum (rank change), breadth (% above 50/200-day averages),
trend, count of new 52-week highs, constituent volume expansion; ranked
strongest to weakest with "emerging leadership" flags. No claims of
institutional accumulation from price and volume.

### 8.4 Momentum score (100 points)
Trend 25, relative strength 20, volume 15, price action 15, sector 10,
momentum indicators 10, risk/reward 5. Every sub-check is stored, so the UI
shows exactly why a stock scored what it did. Missing inputs are N/A and the
score is renormalised with a coverage figure. A liquidity and data-quality
gate runs first; a stock failing it is not scored. Bands: 85+ priority,
75-84 strong, 65-74 developing, below 65 watchlist/excluded. The score never
bypasses the risk engine.

### 8.5 Strategies (independent, each with its own rules)

| | Breakout | Pullback | Continuation |
|---|---|---|---|
| Context | Weekly uptrend; close above 20 and 50 EMA | Daily and weekly uptrend | Strong established trend |
| Trigger | Close above 20- or 50-day resistance, RVOL >= 1.5, positive RS | Pullback to 20/50 EMA on lighter volume, bullish confirmation bar | Consolidation with volatility contraction, RS strong, renewed volume, price confirmation |
| Not allowed | Close more than X ATR above breakout level | Close below 50 EMA, distribution day | Extended above 20 EMA |
| Entry | Next session open, within entry band | Next open after confirmation bar | Next open after expansion bar |
| Initial stop | Below breakout structure, ATR-bounded | Below pullback swing low / 50 EMA | Below consolidation low |
| Invalidation | Close back inside base | Close below swing low | Close below consolidation |
| Exits | Partial at 2R, trail remainder (chandelier / 20 EMA close), time stop 8 weeks | same structure | same structure |

A stock may carry only one active signal at a time across strategies
(priority: Breakout, Continuation, Pullback). Signal states: early warning,
potential setup, confirmed, invalidated, expired.

### 8.6 Market regime classifier
Inputs: Nifty 50 and Nifty 500 trend structure, breadth (% above 50/200-day),
sector participation, India VIX level and change, RS distribution. Output:
strong bullish, moderately bullish, sideways, moderately bearish, strong
bearish. Each regime maps to a policy: maximum new entries per day, risk per
trade multiplier, minimum score, and whether new entries are allowed at all.
Deterioration reduces new-entry eligibility; it never force-closes positions
by itself.

### 8.7 Risk engine (hard gate, every order passes it)
Checks in order, all logged to `risk_events`:
1. Data quality and liquidity gate for the symbol.
2. Regime policy allows new entries.
3. Position size = floor(equity x risk% / (entry - stop)), adjusted for volatility.
4. Individual exposure <= 20% of equity.
5. Sector exposure <= 30% after the trade.
6. Open positions < 5; no duplicate position or pending order in the symbol.
7. Aggregate initial risk <= 2.5% of equity.
8. Cash after the trade >= 20% reserve.
9. Correlation with open positions (60-day returns) below a limit.
10. Gap risk: stop distance versus the stock's historical overnight gap distribution.
Drawdown alerts at 3% and 5% from the equity peak.

### 8.8 Paper execution and portfolio accounting
Orders are created from confirmed signals after the session close and filled
at the **next bar's open** with configurable slippage (bps of price, wider
for low liquidity). Charges follow the Indian delivery cost structure (STT,
exchange transaction charges, SEBI fee, stamp duty, GST, DP charge;
brokerage configurable) in a versioned cost model. Every order carries an
idempotency key (strategy, symbol, signal id) so it cannot be executed
twice. Supports partial exits, trailing stops, time stops and manual
rebalancing. Every state change writes a journal row with the reason and
the data timestamp it relied on.

### 8.9 Backtesting
An event-driven daily simulator that calls the same strategy, risk and
execution modules through a simulated clock and point-in-time universe
snapshots. Ranges: 1, 3, 5 years or custom. Metrics: CAGR, maximum drawdown,
Sharpe, Sortino, win rate, profit factor, expectancy, average winner and
loser, holding period, trade count, turnover, and Nifty 50 benchmark.
Walk-forward (rolling in-sample/out-of-sample windows), out-of-sample hold-out
and parameter sensitivity grids. Results are stored in `backtest_runs` with
the exact configuration and data version, and always presented as historical
simulation, never as expected returns.

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
| `sector_data` | sector, session_date, rs_1m..rs_12m, breadth, new_highs, rank, rotation | |
| `momentum_scores` | isin, session_date, total, coverage, breakdown (JSONB), version | Full sub-check breakdown |
| `trade_signals` | id, isin, strategy, state, session_date, entry_low/high, stop, targets, reasons (JSONB) | State machine history in `signal_events` |
| `paper_orders` | id, idempotency_key (unique), side, qty, type, limit, status, created_at, filled_at, fill_price, slippage, charges | |
| `paper_positions` | id, isin, qty, avg_price, initial_stop, trailing_stop, opened_at, closed_at, strategy, initial_risk | |
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

## 11. Frontend

React + TypeScript single-page app built mobile-first (tested at 360 px
Android widths). Pages: Command Centre, Market Overview, All-Stock Scanner,
Top 20 Opportunities, Breakout Watchlist, Pullback Watchlist, Sector Rotation,
Paper Portfolio, Intraday Monitoring, Risk Analytics, Backtesting Lab, Trade
Journal, AI Daily Report, Settings. Stock cards show symbol, price, data
timestamp and source, score, RS, RVOL, trend status, setup, entry,
invalidation, risk/reward and reasons. Charts use `lightweight-charts`
candlesticks with EMA and volume overlays. No credentials reach the browser.

## 12. AI research layer (optional, Phase 10)

* Python `anthropic` SDK, called only from the backend; key from the
  environment (`ANTHROPIC_API_KEY`), never stored in the database or sent to
  the browser.
* Default model `claude-opus-5-5` with adaptive thinking and streaming for
  long reports; server-side refusal fallback enabled by default; effort
  configurable.
* Input is a JSON snapshot assembled by the application (scores, prices,
  timestamps, sources). The prompt forbids new numbers, predictions and trade
  instructions.
* Output checks: every number in the narrative must appear in the input
  snapshot; otherwise the paragraph is rejected and the report falls back to
  the deterministic template.
* The AI cannot place orders, change settings, size positions or alter
  signals. With no key configured the reports are produced from templates.

## 13. Security

Secrets only in environment variables or a secrets manager, `.env` ignored by
git, `.env.example` documents names only. No broker order client exists in
the codebase. CORS restricted to the dashboard origin. Settings changes are
audited. Dependencies pinned.

## 14. Observability

Structured JSON logs; `data_provider_logs` for lineage; `risk_events` and
`trade_journal` for decisions; `/api/health` reports database, data freshness
and scheduler heartbeats; stale-data and job-failure alerts.

## 15. Deployment

Docker Compose services: `db` (PostgreSQL 16 with a named volume), `api`
(FastAPI), `scheduler` (same image, scheduler entry point), `web` (static
frontend behind nginx). Migrations run with `alembic upgrade head` before the
API starts. Backups: nightly `pg_dump` with retention; restore with
`pg_restore`. Documented in `docs/DEPLOYMENT.md` (Phase 11).

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
* Docker cannot be executed in this sandbox; container deployment is
  validated statically and must be smoke-tested on the target host.

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
| 22 Acceptance | `docs/PHASES.md` acceptance table | 11 |
