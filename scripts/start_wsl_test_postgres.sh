#!/usr/bin/env bash
# Isolated Zest integration-test PostgreSQL (WSL user-space cluster).
# Not architecture. Not a production database. Destructive tests may TRUNCATE this DB.
#
# Does not use the system PostgreSQL cluster and does not require sudo.
# Data directory is outside the repository (OneDrive-safe).

set -euo pipefail

BIN="${ZEST_PG_BIN:-/usr/lib/postgresql/18/bin}"
DATA="${ZEST_PG_TEST_DATA:-$HOME/.local/share/zest-pg-test}"
PORT="${ZEST_PG_TEST_PORT:-55432}"
USER_NAME="${ZEST_PG_TEST_USER:-zest_test}"
DB_NAME="${ZEST_PG_TEST_DB:-zest_test}"

if [[ ! -x "${BIN}/initdb" || ! -x "${BIN}/pg_ctl" ]]; then
  echo "PostgreSQL 18 server binaries not found at ${BIN}" >&2
  echo "Install postgresql-18 in WSL, or set ZEST_PG_BIN." >&2
  exit 1
fi

mkdir -p "$(dirname "${DATA}")"

if [[ -d "${DATA}" ]]; then
  "${BIN}/pg_ctl" -D "${DATA}" stop -m fast >/dev/null 2>&1 || true
fi

if [[ "${1:-}" == "stop" ]]; then
  echo "stopped (if running): ${DATA}"
  exit 0
fi

if [[ ! -f "${DATA}/PG_VERSION" ]]; then
  rm -rf "${DATA}"
  "${BIN}/initdb" \
    -D "${DATA}" \
    --auth-local=trust \
    --auth-host=trust \
    --username="${USER_NAME}" \
    --encoding=UTF8 \
    --locale=C
  {
    echo "listen_addresses = '127.0.0.1'"
    echo "port = ${PORT}"
    echo "unix_socket_directories = '${DATA}'"
  } >> "${DATA}/postgresql.conf"
fi

"${BIN}/pg_ctl" -D "${DATA}" -l "${DATA}/logfile" start
sleep 0.5

if ! "${BIN}/psql" -h 127.0.0.1 -p "${PORT}" -U "${USER_NAME}" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  "${BIN}/createdb" -h 127.0.0.1 -p "${PORT}" -U "${USER_NAME}" "${DB_NAME}"
fi

"${BIN}/psql" -h 127.0.0.1 -p "${PORT}" -U "${USER_NAME}" -d "${DB_NAME}" -c \
  "SELECT current_database() AS db, current_user AS role;"

echo
echo "Destructive integration tests may TRUNCATE this database."
echo "ZEST_TEST_DATABASE_URL=postgresql+psycopg://${USER_NAME}@127.0.0.1:${PORT}/${DB_NAME}"
