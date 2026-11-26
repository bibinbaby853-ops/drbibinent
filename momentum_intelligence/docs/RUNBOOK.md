# Operations runbook

Commands run from `momentum_intelligence/` on the server, with the Docker deployment from
`docs/DEPLOYMENT.md`. For a local (non-Docker) installation, drop `docker compose exec api` and `--compose`.

## 1. Daily rhythm (Asia/Kolkata)

| Time | Job | What it does | Done when |
|---|---|---|---|
| 08:30 Mon-Fri | `pre_market` | pre-market report from the last completed session | report stored |
| 09:30-15:45 Mon-Fri, every 15 min | `intraday` | alerts on completed 15-minute candles (Yahoo, delayed) | one run per slot |
| 18:00-23:40, every 20 min | `eod` | waits for the NSE bhavcopy, then ingest → official index closes (waits until 22:00 IST for them) → universe → scores → signals → paper accounts → post-market report → optional AI commentary | `success` once per session; `skipped` until NSE publishes |

Each run is recorded in `job_runs` (dashboard **Intraday Monitoring** → jobs, or `GET /api/v1/jobs`). A
successful run is never repeated for the same session or slot, and a failed step resumes where it stopped on
the next trigger.

## 2. Is it healthy?

```bash
curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

`status` is `ok` or `degraded`, with `problems` explaining why:

| Problem | Meaning | Action |
|---|---|---|
| `database unreachable` | PostgreSQL down | `docker compose ps`, `docker compose logs db`, `docker compose restart db` |
| `data stale: N expected sessions missing` | more than 2 weekdays without an NSE session (one holiday is tolerated) | check `eod` runs and `docker compose logs scheduler`; NSE archive reachable? |
| `N job failure(s) in 24 h` | a job raised an error | `GET /api/v1/jobs` for the error, then the scheduler log; rerun with `once` (below) |
| `NIFTY 50: no close for <date>; the previous close was carried forward` | neither NSE's official index file nor Yahoo had a bar when the session was scored (after the 22:00 IST wait); regime and relative strength used the previous close | if the message adds "official close now stored", rescore and rebuild (section 4, "Recalculate after a data correction") |

The dashboard shows the same problems in a banner at the top of every page. For unattended monitoring, point
an uptime checker at `https://<domain>/api/health` and alert when the body does not contain `"status":"ok"`.

Run the whole acceptance check at any time:

```bash
MI_TOKEN=<token> python3 scripts/acceptance.py http://127.0.0.1:8000
```

## 3. Backups

### Schedule

```cron
# crontab -e (server user that can run docker), 02:30 IST daily, after the evening pipeline
30 2 * * * cd /opt/momentum/momentum_intelligence && scripts/backup.sh --compose >> backups/backup.log 2>&1
```

`backup.sh` writes `backups/momentum-<UTC time>.dump` (PostgreSQL custom format, about 400 MB) with a
`.sha256`, checks that the archive is readable, and keeps the newest 14 (`MI_BACKUP_KEEP`). The folder is
created with mode 700 and the files with 600.

**Keep copies off the server.** A backup on the same disk does not survive losing the server. Copy the
newest dump and its `.sha256` to other storage, for example with `rclone copy` or `rsync`, after each run.

Raw NSE downloads (`/srv/data` in the `midata` volume) can always be downloaded again, so they are optional
to back up: `docker compose cp api:/srv/data ./backups/data-$(date +%F)`.

### Monthly drill

```bash
scripts/restore_drill.sh --compose
```

The drill backs up, restores into a throw-away database, compares every table's row count and content hash
with the live database, prints `"identical": true` or the differing tables, and drops the copy. It is
read-only for the live database. Phase 11 result: 30 tables and 12,028,904 rows identical in about 110 seconds,
both locally and in Docker.

### Restore (disaster recovery)

```bash
docker compose stop api scheduler
scripts/restore.sh backups/momentum-<time>.dump --target momentum --compose --replace --yes
docker compose start api scheduler
curl -s http://127.0.0.1:8000/api/health
```

`restore.sh` verifies the checksum first, refuses without `--yes`, and refuses to overwrite a database that
has tables unless `--replace` is given. The scheduler then catches up any sessions published since the backup
on its next evening run, or immediately with `docker compose exec scheduler python -m app.scheduler.service once eod`.

## 4. Common tasks

```bash
docker compose exec scheduler python -m app.scheduler.service once eod          # run a job now (pre_market | intraday | eod)
docker compose exec api python -m app.market_data.cli status                    # data coverage
docker compose exec api python -m app.portfolio.cli show --name fwd-D5          # a paper account
docker compose logs --since 24h scheduler | grep -E "ERROR|failed"
docker compose restart api scheduler
```

### Recalculate after a data correction

When stored data for a session was corrected (for example an official index close that arrived late):

```bash
docker compose exec api python -m app.scoring.cli run --start <first corrected session>
docker compose exec api python -m app.strategies.cli build          # default settings
docker compose exec scheduler python -m app.scheduler.service once eod   # rebuilds signals for account profiles
```

Paper accounts that already traded on the old figures keep their history unless reset:
`python -m app.portfolio.cli reset --name <account> --start-after <date> --reason "<why>" --yes`, then
`python -m app.portfolio.cli run --name <account> --auto-signals`. The reset is recorded in the account's
journal with every discarded order. Take a backup first.

### Rotate the API token

1. `python3 -c "import secrets; print(secrets.token_urlsafe(36))"`
2. Put it in `.env` as `MI_API_TOKEN`.
3. `docker compose up -d api` (recreates the container with the new value).
4. Enter the new token on the dashboard's **Settings** page.

### Change the database password

The password is set when the database volume is first created, so changing `.env` alone is not enough:

```bash
docker compose exec db psql -U momentum -c "ALTER ROLE momentum PASSWORD '<new>'"
# then set MI_DB_PASSWORD=<new> in .env and:
docker compose up -d api scheduler
```

### Locked out by the login throttle

Ten different wrong tokens from one address within 5 minutes block that address for the rest of the window.
Wait 5 minutes, or `docker compose restart api` to clear it. Check the token in `.env` if this happens
unexpectedly: repeated guesses mean someone is trying tokens.

## 5. Troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| `api` restarting, log says `refusing to start in production` | `.env` is missing the token, has a default password, or `MI_DATA_MODE=demo` | the message lists each problem |
| `eod` stays `skipped` after 23:40 | NSE did not publish (holiday) or the archive is unreachable | `docker compose exec api python -m app.market_data.cli --live ingest --start <d> --end <d>` |
| Dashboard pages show "401" | token missing or changed | enter it on Settings |
| Dashboard shows "429" | login throttle (above) | wait 5 minutes |
| Report shows no AI commentary | AI off, or commentary rejected by the number check | report page shows the status; `GET /api/v1/reports/<id>` |
| Disk filling | Docker logs rotate; backups keep 14 | `du -sh backups`, `docker system df` |
