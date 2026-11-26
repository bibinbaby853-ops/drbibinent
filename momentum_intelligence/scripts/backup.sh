#!/usr/bin/env bash
# Back up the PostgreSQL database (custom format: compressed, restorable table by table).
#   scripts/backup.sh            # local database from MI_DATABASE_URL
#   scripts/backup.sh --compose  # the docker compose `db` service (run from momentum_intelligence/)
# Writes $MI_BACKUP_DIR/momentum-<UTC time>.dump plus .sha256, and keeps the newest $MI_BACKUP_KEEP (default 14).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_pg_env.sh
load_dotenv
MODE=local; [ "${1:-}" = "--compose" ] && MODE=compose
DIR="${MI_BACKUP_DIR:-./backups}"; KEEP="${MI_BACKUP_KEEP:-14}"
mkdir -p "$DIR"; chmod 700 "$DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"; OUT="$DIR/momentum-$STAMP.dump"
if [ "$MODE" = local ]; then
  pg_env_from_url "${MI_DATABASE_URL:?MI_DATABASE_URL is not set}"
  DB="$PGDATABASE"
else
  DB="${MI_DB_NAME:-momentum}"
fi
umask 077
pg_run pg_dump -Fc -Z 6 --no-owner -d "$DB" > "$OUT.partial"
mv "$OUT.partial" "$OUT"
( cd "$DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256" )
# check the archive is readable before declaring success
if [ "$MODE" = local ]; then pg_restore -l "$OUT" > /dev/null; else docker compose exec -T db pg_restore -l < "$OUT" > /dev/null; fi
ls -1t "$DIR"/momentum-*.dump | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old" "$old.sha256"; done
echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
