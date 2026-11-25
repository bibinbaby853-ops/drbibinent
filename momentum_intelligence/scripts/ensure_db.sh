#!/usr/bin/env bash
# Start the local PostgreSQL if it is not running (sandbox containers can restart without it).
set -euo pipefail
if ! pg_isready -h localhost -p 5432 -q 2>/dev/null; then
  service postgresql start >/dev/null
  for _ in $(seq 1 30); do pg_isready -h localhost -p 5432 -q && break; sleep 1; done
fi
pg_isready -h localhost -p 5432
