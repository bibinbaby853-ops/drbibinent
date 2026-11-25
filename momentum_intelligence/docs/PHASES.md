# Delivery Phases

Each phase ends with: what was implemented, files, tests run and results,
failures, missing dependencies, and owner confirmation before major
architectural changes. Nothing is reported as working unless its tests pass.

| Phase | Scope | Exit criteria (tests that must pass) | Status |
|---|---|---|---|
| 1 | Architecture, requirements, runnable skeleton, validated configuration, health API, environment checker, Docker files | Config validation tests, API health tests, Postgres connectivity test, `docker compose config` valid | **Done** (confirmed) |
| 2 | Market data engine: provider interface, NSE archive, Yahoo, Demo (Kite adapter stub until host allowed), validation, lineage, raw/adjusted storage, Alembic schema for data tables | Missing/duplicate/stale/OHLC-inconsistency detection, corporate-action factor derivation, provider retry and rate-limit tests, ingest into Postgres | **Done** (confirmed) |
| 3 | Security master, point-in-time universe snapshots, indicator engine | Reference values for every indicator, no look-ahead, liquidity filter (median ₹5 Cr configurable), snapshot reproducibility | **Done** (confirmed) |
| 4 | Relative strength, sector rotation, regime classifier, 100-point score with breakdown | RS normalisation, sector ranking, regime fixtures for all five states, score weights and gates | **Done** (confirmed) |
| 5 | Strategy engine (Breakout, Pullback, Continuation), signal state machine, de-duplication | Rule fixtures per strategy, invalidation, duplicate suppression | **Done, awaiting confirmation** |
| 6 | Risk engine and persistent paper portfolio | Position sizing, every limit, sector exposure, cash reserve, aggregate risk, correlation, idempotent execution, charges, partial exits, trailing stops, accounting reconciliation | **Done** (confirmed; built before Phase 5 at the owner's request) |
| 7 | Backtesting (event-driven, walk-forward, out-of-sample, sensitivity, metrics vs Nifty 50) | Metric formulas on fixtures, no look-ahead, point-in-time universe used, cost and slippage applied | Not started |
| 8 | Dashboard (14 pages, mobile-first, charts) | API contract tests, UI build, Android-width screenshots | Not started |
| 9 | Intraday monitoring, scheduler, alerts, automated reports | Completed-candle only, alert states, job idempotency, report timestamps and sources | Not started |
| 10 | Optional Claude research layer | Grounding check rejects unsupported numbers; works with AI disabled | Not started |
| 11 | Hardening, deployment, backup/restore, docs | Full test suite, migration from empty DB, backup/restore drill, acceptance checklist | Not started |

## Final acceptance checklist (brief section 22)

| # | Criterion | Verified by | Phase |
|---|---|---|---|
| 1 | Scans the entire eligible NSE universe | Universe snapshot count and scanner run | 3-4 |
| 2 | Transparent ranking | Score breakdown API and UI | 4, 8 |
| 3 | Positional opportunities | Strategy signals | 5 |
| 4 | Intraday monitoring | Alert tests on completed candles | 9 |
| 5 | Persistent ₹100,000 paper portfolio | Portfolio accounting tests | 6 |
| 6 | Risk-based sizing | Sizing tests | 6 |
| 7 | Holdings and P&L | Portfolio snapshot tests | 6 |
| 8 | Weakening momentum | RS/score deterioration alerts | 4, 9 |
| 9 | Daily reports | Report generation tests | 9 |
| 10 | Backtests | Backtest metric tests | 7 |
| 11 | Data-quality issues detected | Data validation tests | 2 |
| 12 | Android browsers | Mobile viewport checks | 8 |
| 13 | Works without AI | Test suite with AI disabled | 10 |
| 14 | No live orders | Static check: no broker order client; config rejects non-paper | 1 onward |
