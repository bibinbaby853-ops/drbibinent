# Deployment

Momentum Intelligence runs as three containers: PostgreSQL (`db`), the API and dashboard (`api`), and the
scheduler (`scheduler`), plus an optional Caddy HTTPS proxy (`proxy`). Everything was built and run in
production mode during Phase 11. The results are in `docs/ACCEPTANCE.md`.

Paper trading only: no part of the deployment can place a broker order.

## 1. Server

* Linux with Docker Engine 24+ and the Compose plugin.
* 2 vCPU, 4 GB RAM and 20 GB disk are comfortable. The database is about 400 MB compressed for five years of
  NSE history, and raw downloads take about 600 MB.
* Outbound HTTPS to `nsearchives.nseindia.com` and `archives.nseindia.com` (end-of-day data) and
  `query1.finance.yahoo.com` / `query2.finance.yahoo.com` (indices, intraday). Add `api.anthropic.com` only if
  the optional commentary is on.
* Inbound: ports 80 and 443 for the HTTPS proxy. Nothing else needs to be public. The API port is bound to
  127.0.0.1.
* Set the server clock to NTP. Schedules run in Asia/Kolkata whatever the server time zone.

## 2. Configure

```bash
git clone <repository> /opt/momentum && cd /opt/momentum/momentum_intelligence
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(36))"   # run twice: database password, API token
```

Edit `.env`:

| Variable | Production value |
|---|---|
| `MI_APP_ENV` | `production` |
| `MI_DATA_MODE` | `live` |
| `MI_DB_PASSWORD` | random (from the command above) |
| `MI_API_TOKEN` | random, at least 32 characters |
| `MI_DOMAIN` | your domain, e.g. `momentum.example.in`, with its DNS A record pointing at the server |
| `MI_ANTHROPIC_API_KEY`, `MI_AI_ENABLED` | only if you want the AI commentary |

`MI_DATABASE_URL` is not needed with Docker; compose builds it from the variables above.

In production, the API and the scheduler refuse to start, with a message naming each problem, if the token
is missing or short, the database password is a default or example value, the data mode is `demo`, or AI is
enabled without a key.

## 3. Start

```bash
docker compose build
docker compose --profile https up -d        # db, api, scheduler and the HTTPS proxy
docker compose ps                           # api should be "healthy"
curl -s http://127.0.0.1:8000/api/health    # on the server; "status": "ok"
```

The `api` container applies database migrations (`alembic upgrade head`) on every start, and the scheduler
waits for it. Open `https://<MI_DOMAIN>`, go to **Settings** and enter the API token. It is kept in that
browser tab's session storage only.

Without a domain (for example on a home network behind a VPN), leave out `--profile https` and reach the
dashboard via an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 server`, then `http://localhost:8000`.

## 4. First data load

A new database is empty. Either restore a backup from an existing installation:

```bash
docker compose stop api scheduler
scripts/restore.sh backups/momentum-<time>.dump --target momentum --compose --replace --yes
docker compose start api scheduler
```

or load history from the exchange archives. The full run takes several hours, mostly polite download delays:

```bash
docker compose exec api python -m app.market_data.cli --live ingest --start 2021-06-01 --end <yesterday>
docker compose exec api python -m app.market_data.cli --live reference
docker compose exec api python -m app.market_data.cli --live indices
docker compose exec api python -m app.market_data.cli --live adjust --confirm-yahoo
docker compose exec api python -m app.universe.cli snapshot --start 2021-06-01 --end <yesterday> --indicators
docker compose exec api python -m app.scoring.cli run --start 2021-07-01
docker compose exec api python -m app.strategies.cli build
```

From then on the scheduler keeps everything current. Runs are pre-market at 08:30, intraday alerts every
15 minutes from 09:30 to 15:45, and the end-of-day pipeline from 18:00, retried every 20 minutes until NSE
publishes.

## 5. Security model

* **One user, one token.** All data endpoints need `Authorization: Bearer <MI_API_TOKEN>`. Only
  `/api/health` is open, and it carries counts and dates only.
* **Login throttling.** A client that presents 10 different wrong tokens within 5 minutes is refused (HTTP
  429) for the rest of the window. A stale saved token or no token at all is not counted, so it cannot lock
  you out.
* **HTTPS** comes from Caddy, with automatic certificates and HSTS. The API itself speaks plain HTTP on the
  internal network only.
* **Headers:** a strict Content-Security-Policy (scripts only from the site itself), no framing, `nosniff`,
  no referrer, and `no-store` on API responses. API documentation (`/docs`, `/openapi.json`) is off in
  production.
* **Containers:** the app runs as a non-root user (uid 10001) on a read-only root filesystem, with all Linux
  capabilities dropped and `no-new-privileges`. Logs rotate at 5 × 10 MB per container.
* **Secrets** live only in `.env` on the server. They are never in git, the image, the frontend bundle, API
  responses or logs. Tests check the repository and the frontend source for credentials.
* **Requests** are limited to 1 MB.

## 6. Updating

```bash
cd /opt/momentum && git pull
cd momentum_intelligence
scripts/backup.sh --compose                  # always back up before an update
docker compose build
docker compose --profile https up -d         # migrations run on api start
curl -s http://127.0.0.1:8000/api/health
```

**Rollback:** check out the previous commit, rebuild, then restore the pre-update backup (section 4). This is
needed because a newer migration may have changed the schema. Migrations also have downgrades
(`docker compose exec api alembic downgrade <revision>`), but restoring the backup is the tested path.

## 7. Backups

See `docs/RUNBOOK.md` section 3. In short: `scripts/backup.sh --compose` nightly from cron, copies kept off
the server, and `scripts/restore_drill.sh --compose` monthly.

## 8. Building behind a TLS-inspecting proxy

If package downloads fail certificate checks during `docker compose build`, as on corporate networks or in
some sandboxes, build the image directly with the proxy's CA bundle as a build secret. It is used only while
installing packages and is not copied into the image:

```bash
docker build --network host --build-arg HTTPS_PROXY \
  --secret id=extra_ca,src=/path/to/ca-bundle.crt -t momentum-intelligence:latest .
docker compose up -d --no-build
```

## 9. Updating dependencies

`backend/requirements.txt` holds the allowed ranges. `backend/requirements.lock` holds the exact versions the
image installs, which are also the versions the test suite was run with inside the image. After changing
`requirements.txt`, regenerate the lock in the same base image and rerun the tests in the new image:

```bash
cd backend
docker run --rm -v "$PWD/requirements.txt:/r.txt:ro" python:3.13-slim \
  sh -c "pip install -q -r /r.txt >/dev/null && pip freeze" > requirements.lock.new
# review the diff, add the two header comment lines, replace requirements.lock, rebuild, then run the tests
# in the image. Tests EMPTY tables: run them only on a development machine with its own momentum_test
# database on localhost (scripts/dev_db_setup.sh), never on the production server.
docker run --rm --network host -v "$PWD/tests:/srv/backend/tests:ro" -v "$PWD/../scripts:/srv/scripts:ro" \
  momentum-intelligence:latest python -m pytest -q -p no:cacheprovider tests
```
