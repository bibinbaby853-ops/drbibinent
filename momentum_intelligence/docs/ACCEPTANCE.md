# Acceptance record (Phase 11, 26 Nov 2026)

These are evidence for the brief's acceptance criteria (section 22) and the Phase 11 exit criteria, all run on
the final code. "Production stack" means the Docker image built from this repository, run with
`MI_APP_ENV=production`, `MI_DATA_MODE=live`, a random token and database password, an empty database
migrated on start, and then the real 5-year NSE dataset restored from a backup.

Re-run any of this with the commands shown. The results below are not claims about the future: data
freshness and the intraday run depend on the scheduler running through a trading day.

## Phase 11 exit criteria

| Criterion | Result | How |
|---|---|---|
| Full test suite | **259 passed** locally; **257 passed, 2 skipped** inside the production image. The skips need git and the PostgreSQL client tools, which are absent from the image by design; both run locally | `python -m pytest -q` in `backend/`; in-image command in `docs/DEPLOYMENT.md` §9 |
| Migration from an empty database | **Pass.** Upgrades to head, matches the models exactly (autogenerate diff empty), downgrades to base, upgrades again. The production `api` container also migrated an empty database on first start | `tests/test_migrations.py` |
| Backup/restore drill | **Pass, identical.** 30 tables and 12,028,904 rows matched by row count and content hash, in about 110 s, run three times: locally, and twice in Docker on the production stack | `scripts/restore_drill.sh [--compose]` |
| Disaster-recovery restore | **Pass.** A 394 MB backup was restored into a fresh production stack in 50 s; the dashboard and API served the restored data | `scripts/restore.sh … --replace --yes` |
| Data survives restarts | **Pass.** `docker compose down` then `up`: same data, no migrations re-run | |
| Insecure production refused | **Pass.** The API and scheduler containers exit, naming each problem (missing token, default password, demo data) | `tests/test_hardening.py`, manual container run |
| HTTPS | **Pass** with `MI_DOMAIN=localhost` (Caddy's internal CA): HTTP/2, HSTS, HTTP→HTTPS redirect, security headers passed through, the API sees forwarded client addresses. A public certificate needs a real domain and was not tested | `docker compose --profile https up -d` |
| Live data from a read-only container | **Pass.** As the app user on a read-only root filesystem: NSE bhavcopy for 25 Nov (2,982 rows) and Yahoo Nifty 50 bars fetched | |

## Brief acceptance criteria

Run on the production stack with `MI_TOKEN=… python3 scripts/acceptance.py http://127.0.0.1:8010`.
Result: **12 pass, 1 warn, 0 fail.**

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Scans the entire eligible NSE universe | PASS | 2,982 NSE candidates, 935 eligible, 935 scanner rows, session 2026-11-25 |
| 2 | Transparent ranking | PASS | GLAND: 28 recorded checks with rules; points sum 94.0 = total 94.0 |
| 3 | Positional opportunities | PASS | 217 signals for 2026-11-25 (breakout, pullback, continuation); every confirmed one has entry, stop and invalidation |
| 4 | Intraday monitoring | **WARN** | Endpoint up and the completed-candle rule is enforced (`test_alerts.py`), but **no intraday run is recorded**: no scheduler has run through market hours in this sandbox. It will be proved on the server's first trading day (`GET /api/v1/jobs`) |
| 5 | Persistent ₹1,00,000 paper portfolio | PASS | 8 paper accounts, all ₹1,00,000 initial capital; ledger reconciles for 8/8 |
| 6 | Risk-based sizing | PASS | 0.5% risk per position; highest aggregate initial risk 0.7% against the 2.5% limit |
| 7 | Holdings and P&L | PASS | Every account: equity = cash + marked value of the holdings; unrealised P&L = sum over positions |
| 8 | Weakening momentum | PASS | RS-lag alert (3%) and exit rules configured; behaviour in `test_alerts.py`, `test_risk_portfolio_units.py` |
| 9 | Daily reports | PASS | Post-market report for 2026-11-25 with 4 named sources and data timestamp |
| 10 | Backtests | PASS | 7 stored runs (single, sensitivity, walk-forward, redesign, holdout) with metrics. Finding: **no out-of-sample edge** (ARCHITECTURE 8.9) |
| 11 | Data-quality issues detected | PASS | Exclusions with reasons, e.g. 23 unconfirmed corporate actions, 60 GSM; validation tests |
| 12 | Android browsers | PASS | 15/15 pages at 360 px on the production container, with the token and the strict CSP (a CSP violation would fail the check) |
| 13 | Works without AI | PASS | AI disabled; all reports and pages complete; `test_ai_layer.py` |
| 14 | No live orders | PASS | `execution_mode=paper`; configuration accepts only `paper`; no broker order code (`test_no_live_trading.py`) |

## Open items

* **ADR-018 (decision needed).** Health on the production stack reports `degraded`: Yahoo's last Nifty
  50 / Nifty 500 / India VIX bar is 23 Nov, so 25 Nov regime and relative strength used carried values. The
  official Nifty 50 close on 25 Nov was 25,819.25, against 25,349.65 carried (1.85% higher). Proposed fix in
  `docs/DECISIONS.md`.
* **Live Claude call** not yet verified (Phase 10): needs `MI_ANTHROPIC_API_KEY`.
* **Intraday run** (criterion 4) to be confirmed on the first trading day of a running deployment.
* **Public HTTPS certificate** to be confirmed on the real domain.
