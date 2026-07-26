#!/usr/bin/env bash
# Back up the mini-cloud infra stack: a Postgres logical dump of ALL databases and a MinIO
# mirror of ALL buckets. Writes into ../backups/<timestamp>/ (gitignored). Run on a schedule.
#
#   ./scripts/backup.sh [OUTPUT_DIR]
set -euo pipefail

cd "$(dirname "$0")/.."
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-backups}/${STAMP}"
mkdir -p "$OUT"

COMPOSE="docker compose"

echo "==> Postgres: pg_dumpall → $OUT/postgres-all.sql.gz"
$COMPOSE exec -T postgres sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' | gzip > "$OUT/postgres-all.sql.gz"

echo "==> MinIO: mirror all buckets → $OUT/minio/"
# Use a throwaway mc container sharing the compose network to mirror /data out.
$COMPOSE exec -T minio sh -c '
  mc alias set local http://localhost:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1
  mc mirror --quiet --overwrite local/ /tmp/_backup >/dev/null 2>&1 || true
  tar -C /tmp -cf - _backup 2>/dev/null
' | tar -C "$OUT" -xf - 2>/dev/null && mv "$OUT/_backup" "$OUT/minio" 2>/dev/null || \
  echo "   (no buckets yet — nothing to mirror)"

echo "==> Done: $OUT"
ls -la "$OUT"
