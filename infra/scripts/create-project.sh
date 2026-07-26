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

# Load the same .env compose uses: admin Postgres creds (PGPASSWORD for the scram-authed admin
# role), MinIO root creds, and the bind address. Loopback defaults kept as a fallback.
# shellcheck disable=SC1091
[[ -f .env ]] && set -a && . ./.env && set +a
ADMIN_PW="${POSTGRES_PASSWORD:-postgres}"

# We provision over the network (host:15432 / host:19000), the way you'd talk to any managed
# Postgres/S3 — so `mini new` works from any machine on the LAN, not only the Docker host.
# INFRA_BIND_ADDR is a *bind* address, though: 0.0.0.0 (or empty) means "all interfaces" and is
# not connectable, so map it to loopback. Set INFRA_CONNECT_ADDR to reach a remote host (e.g. the
# Docker host's LAN IP when running this from a second machine).
BIND="${INFRA_BIND_ADDR:-127.0.0.1}"
case "$BIND" in
  0.0.0.0 | "") DEFAULT_CONNECT=127.0.0.1 ;;
  *) DEFAULT_CONNECT="$BIND" ;;
esac
CONNECT_HOST="${INFRA_CONNECT_ADDR:-$DEFAULT_CONNECT}"

# These CLIs replace `docker compose exec` — fail early and actionably if they're missing (libpq's
# psql is commonly keg-only / not on PATH).
command -v psql >/dev/null || { echo "psql not found on PATH (install libpq and put its bin on PATH)" >&2; exit 1; }
command -v mc   >/dev/null || { echo "mc not found on PATH (install the MinIO client 'mc')" >&2; exit 1; }
: "${STORAGE_ACCESS_KEY:?set STORAGE_ACCESS_KEY in infra/.env}"
: "${STORAGE_SECRET_KEY:?set STORAGE_SECRET_KEY in infra/.env}"

# --- Postgres: role + database ---------------------------------------------------------
echo "==> Postgres: role + database '$NAME' (via ${CONNECT_HOST}:15432)"
PGPASSWORD="$ADMIN_PW" psql -v ON_ERROR_STOP=1 \
  -h "$CONNECT_HOST" -p 15432 -U "${POSTGRES_USER:-postgres}" -d postgres <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${NAME}') THEN
    CREATE ROLE "${NAME}" LOGIN PASSWORD '${DB_PW}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE "${NAME}" OWNER "${NAME}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${NAME}')\gexec
SQL

# --- MinIO: bucket ---------------------------------------------------------------------
# Isolated config dir + unique alias so we never touch the user's ~/.mc or race a concurrent run.
echo "==> MinIO: bucket '$NAME' (via ${CONNECT_HOST}:19000)"
MC_CONFIG_DIR="$(mktemp -d)"
trap 'rm -rf "$MC_CONFIG_DIR"' EXIT
ALIAS="prov-${NAME}"
mc --config-dir "$MC_CONFIG_DIR" alias set "$ALIAS" \
  "http://${CONNECT_HOST}:19000" "$STORAGE_ACCESS_KEY" "$STORAGE_SECRET_KEY" >/dev/null
mc --config-dir "$MC_CONFIG_DIR" mb --ignore-existing "${ALIAS}/${NAME}" >/dev/null
mc --config-dir "$MC_CONFIG_DIR" ls "${ALIAS}/${NAME}" >/dev/null && echo '   bucket ready'

cat <<ENV

==> Done. Canonical env for '${NAME}':

  DATABASE_URL=postgresql://${NAME}:${DB_PW}@${CONNECT_HOST}:15432/${NAME}
  STORAGE_ENDPOINT=http://${CONNECT_HOST}:19000
  STORAGE_BUCKET=${NAME}
  STORAGE_ACCESS_KEY=<from infra .env STORAGE_ACCESS_KEY>
  STORAGE_SECRET_KEY=<from infra .env STORAGE_SECRET_KEY>
ENV
