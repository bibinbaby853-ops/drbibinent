# Sourced by backup/restore scripts: turn MI_DATABASE_URL into PG* variables, so the password never appears
# on a command line (visible in `ps`). Usage: pg_env_from_url "$MI_DATABASE_URL"
pg_env_from_url() {
  eval "$(python3 - "$1" <<'PY'
import shlex, sys
from urllib.parse import urlsplit, unquote
u = urlsplit(sys.argv[1].replace("+psycopg", "").replace("+psycopg2", ""))
for k, v in (("PGHOST", u.hostname or "localhost"), ("PGPORT", str(u.port or 5432)),
             ("PGUSER", unquote(u.username or "")), ("PGPASSWORD", unquote(u.password or "")),
             ("PGDATABASE", u.path.lstrip("/"))):
    print(f"export {k}={shlex.quote(v)}")
PY
)"
}

# Run a PostgreSQL client either locally (PG* from MI_DATABASE_URL) or inside the compose `db` container.
pg_run() {
  if [ "${MODE:-local}" = compose ]; then
    docker compose exec -T db "$@" -U "${MI_DB_USER:-momentum}"
  else
    "$@"
  fi
}

load_dotenv() {  # read MI_* from .env without executing it
  [ -f .env ] || return 0
  while IFS='=' read -r k v; do
    # values already in the environment win; `if` (not `&&`) so an already-set last key is not a failure
    case "$k" in MI_*) if [ -z "${!k:-}" ]; then export "$k=$v"; fi ;; esac
  done < <(grep -E '^MI_[A-Z0-9_]+=' .env)
  return 0
}
