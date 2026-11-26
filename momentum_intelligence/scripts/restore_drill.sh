#!/usr/bin/env bash
# Backup/restore drill: back up, restore into a throw-away database, prove the contents are identical by
# fingerprint (row counts + row hashes for every table), then drop the copy. Read-only for the live database.
#   scripts/restore_drill.sh [--compose]
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/_pg_env.sh
load_dotenv
MODE=local; FLAG=""; [ "${1:-}" = "--compose" ] && { MODE=compose; FLAG=--compose; }
DRILL="mi_restore_drill_$(date -u +%Y%m%d%H%M%S)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
start=$(date +%s)
out="$(scripts/backup.sh $FLAG)"; echo "$out"
FILE="${out#backup ok: }"; FILE="${FILE% (*}"
scripts/restore.sh "$FILE" --target "$DRILL" $FLAG --yes
cleanup() {
  if [ "$MODE" = local ]; then pg_env_from_url "$MI_DATABASE_URL"; psql -qd postgres -c "drop database if exists \"$DRILL\" with (force)"
  else docker compose exec -T db psql -U "${MI_DB_USER:-momentum}" -qd postgres -c "drop database if exists \"$DRILL\" with (force)"; fi
}
trap 'cleanup; rm -rf "$WORK"' EXIT
if [ "$MODE" = local ]; then
  (cd backend && python -m app.ops.fingerprint --out "$WORK/live.json")
  (cd backend && python -m app.ops.fingerprint --database "$DRILL" --out "$WORK/restored.json")
  (cd backend && python -m app.ops.fingerprint --compare "$WORK/live.json" "$WORK/restored.json")
else
  docker compose exec -T api python -m app.ops.fingerprint > "$WORK/live.json"
  docker compose exec -T api python -m app.ops.fingerprint --database "$DRILL" > "$WORK/restored.json"
  (cd backend && python3 -m app.ops.fingerprint --compare "$WORK/live.json" "$WORK/restored.json")
fi
echo "drill finished in $(( $(date +%s) - start ))s; restore copy $DRILL dropped"
