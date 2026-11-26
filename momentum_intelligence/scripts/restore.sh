#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh.
#   scripts/restore.sh FILE.dump --target NAME [--compose] [--replace] --yes
# --target  database to restore into (created if missing). Restoring into a new name and checking it
#           (scripts/restore_drill.sh) before replacing the live database is the safe order.
# --replace drop and recreate the target first; required when it already has tables.
#           In compose mode, stop the app first:  docker compose stop api scheduler
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_pg_env.sh
load_dotenv
FILE=""; TARGET=""; MODE=local; REPLACE=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift ;;
    --compose) MODE=compose ;;
    --replace) REPLACE=1 ;;
    --yes) YES=1 ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) FILE="$1" ;;
  esac
  shift
done
[ -f "$FILE" ] || { echo "backup file not found: $FILE" >&2; exit 2; }
[ -n "$TARGET" ] || { echo "--target is required" >&2; exit 2; }
[[ "$TARGET" =~ ^[a-z_][a-z0-9_]*$ ]] || { echo "invalid database name: $TARGET" >&2; exit 2; }
[ "$YES" = 1 ] || { echo "refusing without --yes (this writes to database '$TARGET')" >&2; exit 2; }
if [ -f "$FILE.sha256" ]; then
  ( cd "$(dirname "$FILE")" && sha256sum -c --quiet "$(basename "$FILE").sha256" ) || { echo "checksum mismatch" >&2; exit 1; }
else
  echo "warning: no checksum file next to $FILE" >&2
fi
[ "$MODE" = local ] && pg_env_from_url "${MI_DATABASE_URL:?MI_DATABASE_URL is not set}"
q() { pg_run psql -v ON_ERROR_STOP=1 -qtA -d postgres -c "$1"; }
exists="$(q "select 1 from pg_database where datname='$TARGET'")"
if [ "$exists" = 1 ]; then
  tables="$(pg_run psql -qtA -d "$TARGET" -c "select count(*) from pg_tables where schemaname='public'")"
  if [ "$tables" != 0 ]; then
    [ "$REPLACE" = 1 ] || { echo "database '$TARGET' has $tables tables; add --replace to overwrite it" >&2; exit 1; }
    q "drop database \"$TARGET\" with (force)"
    exists=""
  fi
fi
[ "$exists" = 1 ] || q "create database \"$TARGET\""
if [ "$MODE" = local ]; then
  pg_restore --no-owner --exit-on-error -d "$TARGET" "$FILE"
else
  docker compose exec -T db pg_restore -U "${MI_DB_USER:-momentum}" --no-owner --exit-on-error -d "$TARGET" < "$FILE"
fi
echo "restore ok: $FILE -> $TARGET"
