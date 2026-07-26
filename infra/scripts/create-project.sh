#!/usr/bin/env bash
# Provision a database + role + bucket for one project, by hand. (The scaffolder automates this
# in Phase 4; this script is the manual path and the Phase-1 "DB and bucket created by hand" proof.)
#
#   ./scripts/create-project.sh <name> [db_password]
#
# Creates: Postgres role <name> + database <name> owned by it, and MinIO bucket <name>.
# Prints the canonical DATABASE_URL / STORAGE_* env for the app.
set -euo pipefail

cd "$(dirname "$0")/.."
NAME="${1:?usage: create-project.sh <name> [db_password]}"
[[ "$NAME" =~ ^[a-z][a-z0-9_-]*$ ]] || { echo "name must be [a-z][a-z0-9_-]*" >&2; exit 1; }
DB_PW="${2:-$NAME}"
COMPOSE="docker compose"

# The admin psql runs inside the container over the local socket, which is scram-authed — so it
# needs PGPASSWORD. Load it from the same .env compose uses (loopback default kept as a fallback).
# shellcheck disable=SC1091
[[ -f .env ]] && set -a && . ./.env && set +a
ADMIN_PW="${POSTGRES_PASSWORD:-postgres}"

# --- Postgres: role + database ---------------------------------------------------------
echo "==> Postgres: role + database '$NAME'"
$COMPOSE exec -T -e PGPASSWORD="$ADMIN_PW" postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres' <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${NAME}') THEN
    CREATE ROLE "${NAME}" LOGIN PASSWORD '${DB_PW}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE "${NAME}" OWNER "${NAME}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${NAME}')\gexec
SQL

# --- MinIO: bucket ---------------------------------------------------------------------
echo "==> MinIO: bucket '$NAME'"
$COMPOSE exec -T minio sh -c "
  mc alias set local http://localhost:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null
  mc mb --ignore-existing local/${NAME} >/dev/null
  mc ls local/${NAME} >/dev/null && echo '   bucket ready'
"

BIND="${INFRA_BIND_ADDR:-127.0.0.1}"
cat <<ENV

==> Done. Canonical env for '${NAME}':

  DATABASE_URL=postgresql://${NAME}:${DB_PW}@${BIND}:15432/${NAME}
  STORAGE_ENDPOINT=http://${BIND}:19000
  STORAGE_BUCKET=${NAME}
  STORAGE_ACCESS_KEY=<from infra .env STORAGE_ACCESS_KEY>
  STORAGE_SECRET_KEY=<from infra .env STORAGE_SECRET_KEY>
ENV
