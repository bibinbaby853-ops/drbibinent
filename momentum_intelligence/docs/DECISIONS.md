# Architecture Decision Records

Status key: **Accepted** (low-risk default, reversible), **Needs confirmation**
(owner decision before Phase 2).

## ADR-001 Stack: FastAPI + PostgreSQL + React (Needs confirmation)
Python backend (FastAPI, SQLAlchemy 2, Alembic, pandas), PostgreSQL 16,
React + TypeScript + Vite frontend with `lightweight-charts`. Chosen because
the existing tested analytics are Python, PostgreSQL runs locally here, and
React gives a responsive Android-friendly UI. Alternative considered: a
server-rendered Python UI (Streamlit/Dash); rejected because it is weaker for
a multi-page, mobile, production dashboard.

## ADR-002 Location (Needs confirmation)
The platform lives in `momentum_intelligence/` inside the existing
`drbibinent` repository on branch `claude/nifty-500-stock-scanner-dshdp2`,
next to the existing `nifty500_scanner/`. A separate repository would be
cleaner long term; it can be split out later without code changes.

## ADR-003 Market data sources and licensing (Needs confirmation)
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

## ADR-010 Authentication (Needs confirmation)
Single-user deployment protected by a bearer token from the environment,
intended to sit behind a reverse proxy with TLS. Multi-user accounts are out
of scope unless requested.
