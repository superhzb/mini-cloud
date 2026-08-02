#!/usr/bin/env bash
# Provision the `identity` database for the `mini-cloud-identity` service: a single LOGIN role that
# owns an empty database. That is *all* infra does here — unlike analytics-init, this script applies
# NO schema and wires NO Grafana datasource.
#
# Why the difference: the auth SDK in this repo (`packages/auth`) is a tiny, db-less *verifier* and
# owns no schema; the `grants`/`users` tables belong to the in-repo `mini-cloud-identity` service
# (`services/identity/`, the sole writer), which applies its migrations on boot as this owner. Infra
# just hands the service an empty DB + a role to own it. (See docs/identity-plan.md.)
#
#   ./scripts/identity-init.sh
#
# Idempotent: re-running is safe (creates the role/DB only if absent; never drops).
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose"

DB="identity"
OWNER="identity"                                   # the identity service's writer/owner role
OWNER_PW="${IDENTITY_PASSWORD:-identity}"

# The admin psql runs inside the container over the scram-authed local socket — needs PGPASSWORD.
# shellcheck disable=SC1091
[[ -f .env ]] && set -a && . ./.env && set +a
ADMIN_PW="${POSTGRES_PASSWORD:-postgres}"

echo "==> Postgres: role + database '$DB' (owner: $OWNER)"
$COMPOSE exec -T -e PGPASSWORD="$ADMIN_PW" postgres \
  sh -c 'psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d postgres' <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${OWNER}') THEN
    CREATE ROLE "${OWNER}" LOGIN PASSWORD '${OWNER_PW}';
  END IF;
END \$\$;
SELECT 'CREATE DATABASE "${DB}" OWNER "${OWNER}"'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB}')\gexec
SQL

BIND="${INFRA_BIND_ADDR:-127.0.0.1}"
cat <<ENV

==> Done. The identity database is ready — but empty. The mini-cloud-identity service owns and
    applies the schema (the grants/users tables) on boot; point it at:

  IDENTITY_DATABASE_URL=postgresql://${OWNER}:${OWNER_PW}@${BIND}:15432/${DB}

This is NOT DATABASE_URL for an app, and NOT MINI_AUTH_ISSUER for a verifier — it is the identity
service's private connection to its own store. Apps only ever see the signed JWT (MINI_AUTH_*).
ENV
