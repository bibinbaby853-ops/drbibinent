# Architecture Decision Records

Status key: **Accepted** (low-risk default, reversible), **Needs confirmation**
(owner decision before Phase 2).

## ADR-001 Stack: FastAPI + PostgreSQL + React (Accepted, confirmed by owner)
Python backend (FastAPI, SQLAlchemy 2, Alembic, pandas), PostgreSQL 16,
React + TypeScript + Vite frontend with `lightweight-charts`. Chosen because
the existing tested analytics are Python, PostgreSQL runs locally here, and
React gives a responsive Android-friendly UI. Alternative considered: a
server-rendered Python UI (Streamlit/Dash); rejected because it is weaker for
a multi-page, mobile, production dashboard.

## ADR-002 Location (Accepted, confirmed by owner)
The platform lives in `momentum_intelligence/` inside the existing
`drbibinent` repository on branch `claude/nifty-500-stock-scanner-dshdp2`,
next to the existing `nifty500_scanner/`. A separate repository would be
cleaner long term; it can be split out later without code changes.

## ADR-003 Market data sources and licensing (Accepted, confirmed by owner)
* Now: NSE official end-of-day archives (bhavcopy, delivery, security lists,
  index constituents) as the primary daily source; Yahoo Finance for adjusted
  cross-checks, indices, India VIX and delayed 15/60-minute intraday bars.
  Yahoo and NSE website files are **not** licensed redistribution feeds; they
  are labelled "official EOD" or "unofficial, delayed" everywhere.
* Later: a licensed provider adapter (Zerodha Kite Connect) behind the same
  interface. It needs a Kite Connect subscription, API key and secret, a daily
  login flow for the access token, and the network host `api.kite.trade`
  allowed for this environment (currently blocked).
* DEMO mode with synthetic, clearly labelled data when no provider is
  reachable.

## ADR-004 Survivorship control from historical bhavcopies (Accepted)
Point-in-time universe rebuilt per session from the daily NSE bhavcopy, which
includes later-delisted securities. Five years is about 1,250 files; download
is throttled and cached once.

## ADR-005 Corporate-action adjustment from official data (Accepted)
Adjustment factors are derived where NSE `PREV_CLOSE` differs from the prior
day's close, cross-checked with Yahoo split events. Raw and adjusted series
are stored separately; identity joins use ISIN to survive symbol changes.

## ADR-006 Paper trading only, enforced structurally (Accepted)
No broker order client exists in the codebase. `execution_mode` accepts only
`paper`. Read-only broker data (if ever added) lives in `market_data/` and has
no order methods.

## ADR-007 Scheduler (Accepted)
APScheduler in a dedicated process with Postgres advisory locks; idempotent
jobs keyed by session date. Redis is optional.

## ADR-008 AI layer (Accepted, optional)
Official `anthropic` Python SDK, server-side only, default model
`claude-opus-5-5`, adaptive thinking, streaming for long reports,
server-side refusal fallback on. Narrative only; numbers in output must
match the input snapshot. Needs an `ANTHROPIC_API_KEY` when you want it on.

## ADR-009 Docker in this sandbox (Accepted)
The Docker daemon is not available here. Compose and Dockerfiles are written
and validated statically; local development and tests use the system
PostgreSQL 16. Container smoke tests must run on the deployment host.

## ADR-010 Authentication (Accepted, confirmed by owner)
Single-user deployment protected by a bearer token from the environment,
intended to sit behind a reverse proxy with TLS. Multi-user accounts are out
of scope unless requested.

## ADR-011 Unconfirmed corporate actions quarantine a stock (Accepted, confirmed by owner after Phase 2)
A stock whose look-back window (252 sessions) contains a corporate-action
factor that is inferred but not confirmed by a second source, or that the
second source disputes, is not eligible for signals. Its data is still
stored and shown. Reason code: `unconfirmed_corporate_action`.

## ADR-012 History depth 2021 onward (Accepted, confirmed by owner after Phase 2)
NSE end-of-day history is loaded from 1 Jan 2021, in date order so identity
resolution (symbol and ISIN changes) is correct. This gives five years for
walk-forward backtests.

## ADR-013 Unclassified sector (Accepted, confirmed by owner after Phase 2)
Stocks without a sector from the reachable NSE classification file are
grouped as "Unclassified" and that group is capped like any sector (30%).
Sector rotation analysis excludes the group.

## ADR-014 Point-in-time price adjustment for indicators (Accepted)
Indicators at session d use prices adjusted only for corporate actions with
ex-date <= d (levels equal the raw price on d). Back-adjusting with factors
known only later would leak future information into price-level indicators
(ATR in rupees, stop levels) during backtests. Stored `is_adjusted` rows
(adjusted to the latest session) are for charts and current analysis.

## ADR-015 Surveillance flags are current-only (Accepted)
`sec_list.csv` gives today's price band and GSM stage. ASM lists and history
live on www.nseindia.com, which is not reachable. Flags are stored by fetch
date, so they accumulate point-in-time from now on; backtests before that
cannot apply surveillance filters and say so in their report.

## ADR-016 Strategy evidence and forward testing (Accepted, after the Phase 7 redesign)
Neither the original rules nor the best pre-registered redesign showed an
out-of-sample edge (redesign D5: +18.5% in the design period, -10.6% on the
locked holdout versus Nifty 50 -0.8%). The 2021-2026 history has now been used
for design and for the holdout, so it cannot validate further changes. From
here, strategy variants are evaluated by forward paper trading in parallel
paper accounts, with results reported as they accrue; backtests on the old
history are diagnostics only. The platform is presented as a research and
paper-trading tool with no demonstrated edge.
