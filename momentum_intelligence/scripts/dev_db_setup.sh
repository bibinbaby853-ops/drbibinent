#!/usr/bin/env bash
# Create the local development and test databases (idempotent). Requires a local
# PostgreSQL server and sudo/postgres access. Credentials come from the environment;
# the defaults are for local development only.
set -euo pipefail
DB_USER="${MI_DB_USER:-momentum}"
DB_PASS="${MI_DB_PASSWORD:-momentum}"
for DB in "${MI_DB_NAME:-momentum}" "${MI_DB_TEST_NAME:-momentum_test}"; do
  su postgres -c "psql -v ON_ERROR_STOP=1 -tAc \"SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'\"" | grep -q 1 \
    || su postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}'\""
  # CREATEDB lets the migration test and the restore drill use throw-away databases (local development only)
  su postgres -c "psql -qc \"ALTER ROLE ${DB_USER} CREATEDB\""
  su postgres -c "psql -tAc \"SELECT 1 FROM pg_database WHERE datname='${DB}'\"" | grep -q 1 \
    || su postgres -c "psql -v ON_ERROR_STOP=1 -c \"CREATE DATABASE ${DB} OWNER ${DB_USER}\""
  echo "database ${DB} ready (owner ${DB_USER})"
done
