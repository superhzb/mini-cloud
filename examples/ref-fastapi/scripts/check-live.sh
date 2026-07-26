#!/usr/bin/env bash
# Full validation harness against an EPHEMERAL, throwaway Postgres (scorecard metric #3): the
# check suite must not be coupled to the always-on infra stack being reachable. Boots a disposable
# postgres container, points DATABASE_URL at it, runs the live tests, and tears it down — always.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null 2>&1 || { echo "docker required for check-live" >&2; exit 1; }

CID="ref-fastapi-check-$$"
PORT=55432
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

export DATABASE_URL="postgresql://postgres:throwaway@127.0.0.1:$PORT/postgres"
echo "==> ruff + pyright + pytest (with --run-live against the throwaway DB)"
uv run ruff check .
uv run pyright src
uv run pytest -q --run-live
echo "==> check-live OK"
