#!/usr/bin/env bash
# Restore a backup produced by backup.sh into a running infra stack.
#
#   ./scripts/restore.sh backups/<timestamp>
#
# WARNING: this overwrites current Postgres data. Confirm the target before running.
set -euo pipefail

cd "$(dirname "$0")/.."
SRC="${1:?usage: restore.sh <backup-dir>}"
COMPOSE="docker compose"

[ -f "$SRC/postgres-all.sql.gz" ] || { echo "no postgres-all.sql.gz in $SRC" >&2; exit 1; }

read -r -p "This overwrites Postgres in the running stack. Continue? [y/N] " ok
[ "$ok" = "y" ] || { echo "aborted"; exit 1; }

echo "==> Restoring Postgres from $SRC/postgres-all.sql.gz"
gunzip -c "$SRC/postgres-all.sql.gz" | \
  $COMPOSE exec -T postgres sh -c 'psql -U "${POSTGRES_USER:-postgres}" -d postgres'

if [ -d "$SRC/minio" ]; then
  echo "==> Restoring MinIO buckets from $SRC/minio"
  tar -C "$SRC" -cf - minio | $COMPOSE exec -T minio sh -c '
    tar -C /tmp -xf -
    mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1
    mc mirror --quiet --overwrite /tmp/minio local/ >/dev/null 2>&1 || true
  '
fi

echo "==> Restore complete."
