#!/usr/bin/env bash
# Full validation harness against an EPHEMERAL, throwaway Postgres (scorecard metric #3): the
# check suite must not be coupled to the always-on infra stack being reachable. Boots a disposable
# postgres container, points DATABASE_URL at it, runs the live tests, and tears it down — always.
#
# Storage/inference live tours are NOT exercised here (they need MinIO + a gateway); those run in
# the full end-to-end `make seed-live` path. This harness proves the db + queue tours (including
# dead-letter, heartbeat, and requeue-from-dead-letter) against a disposable Postgres.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null 2>&1 || { echo "docker required for check-live" >&2; exit 1; }

CID="ref-showcase-check-$$"
PORT=55433
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> starting throwaway postgres ($CID) on :$PORT"
docker run -d --name "$CID" -e POSTGRES_PASSWORD=throwaway -p "127.0.0.1:$PORT:5432" \
  postgres:16.4-alpine >/dev/null

echo "==> waiting for readiness"
for _ in $(seq 1 30); do
  if docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done

# The analytics event store is a SEPARATE database in production (its own migration ledger — the
# app's and the package's migrations both start at 0001, so they must not share one DB). Mirror that
# here with a second database in the same throwaway container rather than a second container.
echo "==> creating analytics database"
docker exec "$CID" psql -U postgres -c "CREATE DATABASE analytics" >/dev/null

export DATABASE_URL="postgresql://postgres:throwaway@127.0.0.1:$PORT/postgres"
export MINI_ANALYTICS_DSN="postgresql://postgres:throwaway@127.0.0.1:$PORT/analytics"
# Pin the harness to Postgres only: empty (not unset) so the SDK's import-time load_dotenv can't
# repopulate STORAGE_*/inference from ./.env. This keeps check-live genuinely decoupled from the
# always-on MinIO/gateway — the full-pipeline (storage) + AI tours run in the full-stack e2e path,
# not here. With these off, the storage-dependent test skips and the db + queue tours run.
export STORAGE_ENDPOINT="" STORAGE_BUCKET="" STORAGE_ACCESS_KEY="" STORAGE_SECRET_KEY=""
export MINI_INFERENCE_URL=""
echo "==> ruff + pyright + pytest (with --run-live against the throwaway DB)"
uv run ruff check .
uv run pyright src
uv run pytest -q --run-live
echo "==> check-live OK"
