# Momentum Intelligence

Positional (2-8 week) momentum research and **paper** portfolio management for
NSE equities. Deterministic, auditable rules; risk-first position sizing; no
live order placement anywhere in the codebase.

**Status: Phase 1 complete (architecture and runnable skeleton).** Market data,
scanning, strategies, portfolio, backtesting, dashboard, alerts and AI arrive
in Phases 2-10. See `docs/PHASES.md`.

## Documents

* `docs/ARCHITECTURE.md` - system design, data sources and licensing, schema, API, risk rules, traceability to the brief
* `docs/DECISIONS.md` - architecture decision records, including those awaiting confirmation
* `docs/PHASES.md` - phase plan, exit tests and the final acceptance checklist

## What works today

* Validated configuration with every setting from the brief (capital ₹100,000, 5 positions, 0.5% risk, 20% / 30% exposure caps, 20% cash, 2.5% aggregate risk, ₹5 Cr liquidity, score weights and bands). Inconsistent settings and any non-paper execution mode are rejected at start-up.
* FastAPI service: `GET /api/health`, `GET /api/v1/settings`, OpenAPI docs at `/docs`.
* PostgreSQL connectivity (development and test databases).
* Environment checker and Docker Compose definition (database + API).

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
