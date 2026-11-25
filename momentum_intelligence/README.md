# Momentum Intelligence

Positional (2-8 week) momentum research and **paper** portfolio management for
NSE equities. Deterministic, auditable rules; risk-first position sizing; no
live order placement anywhere in the codebase.

**Status: Phases 1-4 and 6 complete; Phase 6 (risk engine and paper portfolio)
awaiting confirmation. Phase 5 (strategies) comes next and plugs into the same
proposal interface.** Backtesting, dashboard, alerts and AI follow. See `docs/PHASES.md`.

## Documents

* `docs/ARCHITECTURE.md` - system design, data sources and licensing, schema, API, risk rules, traceability to the brief
* `docs/DECISIONS.md` - architecture decision records, including those awaiting confirmation
* `docs/PHASES.md` - phase plan, exit tests and the final acceptance checklist

## What works today

* Validated configuration with every setting from the brief (capital ₹100,000, 5 positions, 0.5% risk, 20% / 30% exposure caps, 20% cash, 2.5% aggregate risk, ₹5 Cr liquidity, score weights and bands). Inconsistent settings and any non-paper execution mode are rejected at start-up.
* FastAPI service: `GET /api/health`, `GET /api/v1/settings`, OpenAPI docs at `/docs`.
* PostgreSQL connectivity (development and test databases).
* Environment checker and Docker Compose definition (database + API).
* Market data engine (`backend/app/market_data/`): NSE official end-of-day archives (primary), Yahoo (unofficial; intraday, indices, split cross-check), labelled DEMO data, and a Kite adapter that honestly reports it is unavailable here. Polite HTTP with rate limits and retries, raw file cache with SHA-256 lineage, data-quality checks, ISIN-based identity across symbol and ISIN changes, corporate-action factors and adjusted series, Alembic schema.

* Universe engine (`backend/app/universe/`): point-in-time eligibility for every session with reason codes (series, liquidity, history, data quality, unconfirmed corporate actions, GSM), reproducible snapshots keyed by rule hash.
* Indicator engine (`backend/app/indicators/`): trend, momentum, volatility, level, volume and weekly indicators, causal and point-in-time adjusted.
* Scoring (`backend/app/scoring/`): relative strength vs Nifty 50 / Nifty 500 / sector with a 1-99 rank and classes; sector rotation from constituent composites; five-state market regime with entry policy and hysteresis; 100-point score with 28 recorded sub-checks.
* Risk engine and paper portfolio (`backend/app/risk/`, `backend/app/portfolio/`): ten ordered pre-trade checks (re-applied at fill), next-open fills with slippage and Indian delivery charges, stops, partial exits, trailing and time stops, split handling, drawdown alerts, daily reconciliation and a full journal. PAPER ONLY.
* Read-only API (bearer token required when `MI_API_TOKEN` is set): `/api/v1/universe`, `/api/v1/stocks/{symbol}/indicators`, `/api/v1/market/regime`, `/api/v1/sectors`, `/api/v1/scores`, `/api/v1/stocks/{symbol}/score`, `/api/v1/portfolio` (+ `/history`, `/orders`, `/proposals`, `/journal`, `/risk-events`; POST `/proposals`, POST `/positions/{id}/exit`).

## Market data commands

```bash
cd backend
alembic upgrade head                                          # create / migrate tables
python -m app.market_data.cli --demo ingest --days 60         # labelled synthetic data
python -m app.market_data.cli --live ingest --start 2026-01-01 --end 2026-11-25
python -m app.market_data.cli --live reference                # names, sectors, inactive flags
python -m app.market_data.cli --live adjust --confirm-yahoo   # check inferred splits against Yahoo
python -m app.market_data.cli status
python -m app.universe.cli snapshot --indicators              # latest session's universe + indicators
python -m app.universe.cli snapshot --start 2021-06-01 --end 2026-11-23
python -m app.universe.cli show
python -m app.market_data.cli --live indices                  # Nifty 50 / 500 / India VIX (Yahoo, unofficial)
python -m app.scoring.cli run --start 2021-07-01              # RS, sectors, regime, scores (latest by default)
python -m app.scoring.cli show
python -m app.portfolio.cli init --name main --capital 100000          # PAPER account
python -m app.portfolio.cli propose --name main --symbol INFY --entry 1100 --stop 1040
python -m app.portfolio.cli run --name main                            # process new sessions
python -m app.portfolio.cli show --name main
```

Re-running any command gives the same database state. Downloaded files are
kept under `data/historical/nse/` (git-ignored); `--offline` reads only those.

## Quick start (local)

```bash
cd momentum_intelligence
python scripts/check_environment.py          # what this machine can do
sudo ./scripts/dev_db_setup.sh               # local PostgreSQL databases (dev + test)
pip install -r backend/requirements.txt
cp .env.example .env                         # then edit values; never commit .env
cd backend && uvicorn app.main:app --reload  # http://localhost:8000/docs
python -m pytest -q                          # from backend/
```

## Docker

```bash
cp .env.example .env    # set MI_DB_PASSWORD
docker compose up -d --build
```

Docker could not be executed in the build sandbox (no daemon); the compose file
is validated with `docker compose config`. Smoke-test it on the target host.

## Safety

* Paper trading only. There is no broker order client; a test fails the build if one appears.
* Secrets are read from the environment, never returned by the API and never committed.
* Data is labelled with its source and licence; nothing is fabricated when data is missing.
* Backtest results are historical simulations, not expected returns.
