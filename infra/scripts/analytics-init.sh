#!/usr/bin/env bash
# Provision the shared `analytics` product-event store: a write role that owns the DB, the schema
# (applied from the SDK package's own migrations — single source of truth), and a SELECT-only role
# for Grafana. Distinct from create-project.sh, which makes a full owning LOGIN role, applies no
# schema, and wires no datasource — here read-only role + schema-apply are both new.
#
#   ./scripts/analytics-init.sh
#
# Idempotent: re-running is safe (CREATE ... IF NOT EXISTS throughout).
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose"

DB="analytics"
OWNER="analytics"                                   # the shared writer; apps set MINI_ANALYTICS_DSN to it
OWNER_PW="${ANALYTICS_PASSWORD:-analytics}"
RO_ROLE="analytics_ro"                              # Grafana's SELECT-only role
RO_PW="${ANALYTICS_RO_PASSWORD:-analytics_ro}"
# The SDK package ships the schema; infra applies the same files so the read-only role + datasource
# work before any app boots. The app's migrate() re-runs them idempotently (CREATE ... IF NOT EXISTS).
MIGRATIONS_GLOB="../packages/analytics/src/mini_cloud/analytics/migrations/*.sql"

# The admin psql runs inside the container over the scram-authed local socket — needs PGPASSWORD.
# shellcheck disable=SC1091
[[ -f .env ]] && set -a && . ./.env && set +a
ADMIN_PW="${POSTGRES_PASSWORD:-postgres}"

echo "==> Postgres: roles + database '$DB'"
$COMPOSE exec -T -e PGPASSWORD="$ADMIN_PW" postgres \
  sh -c 'psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres' <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${OWNER}') THEN
    CREATE ROLE "${OWNER}" LOGIN PASSWORD '${OWNER_PW}';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${RO_ROLE}') THEN
    CREATE ROLE "${RO_ROLE}" LOGIN PASSWORD '${RO_PW}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE "${DB}" OWNER "${OWNER}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB}')\gexec
GRANT CONNECT ON DATABASE "${DB}" TO "${RO_ROLE}";
SQL

echo "==> Schema: applying package-owned migrations to '$DB' (as $OWNER)"
# shellcheck disable=SC2086
cat $MIGRATIONS_GLOB | $COMPOSE exec -T -e PGPASSWORD="$OWNER_PW" postgres \
  sh -c "psql -v ON_ERROR_STOP=1 -U '${OWNER}' -d '${DB}'"

echo "==> Grafana read-only role: SELECT + USAGE + default privileges for future tables"
$COMPOSE exec -T -e PGPASSWORD="$ADMIN_PW" postgres \
  sh -c "psql -v ON_ERROR_STOP=1 -U \"\${POSTGRES_USER:-postgres}\" -d '${DB}'" <<SQL
GRANT USAGE ON SCHEMA public TO "${RO_ROLE}";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "${RO_ROLE}";
-- Future tables/partitions (see the monthly-partitioning note) created by the owner stay readable
-- with no re-grant.
ALTER DEFAULT PRIVILEGES FOR ROLE "${OWNER}" IN SCHEMA public GRANT SELECT ON TABLES TO "${RO_ROLE}";
SQL

BIND="${INFRA_BIND_ADDR:-127.0.0.1}"
cat <<ENV

==> Done. The analytics store is ready. Point apps at it:

  MINI_ANALYTICS_DSN=postgresql://${OWNER}:${OWNER_PW}@${BIND}:15432/${DB}

Grafana reads it read-only via the provisioned 'Analytics' Postgres datasource (uid: analytics).
Restart Grafana to pick up the datasource if the stack was already up: make restart
ENV
