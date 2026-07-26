#!/usr/bin/env bash
# Full validation harness against an EPHEMERAL throwaway Postgres (scorecard #3).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v docker >/dev/null 2>&1 || { echo "docker required for check-live" >&2; exit 1; }

CID="{{name}}-check-$$"
PORT=55432
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> starting throwaway postgres ($CID) on :$PORT"
docker run -d --name "$CID" -e POSTGRES_PASSWORD=throwaway -p "127.0.0.1:$PORT:5432" \
  postgres:16.4-alpine >/dev/null

for _ in $(seq 1 30); do
  docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break
  sleep 1
done

export DATABASE_URL="postgresql://postgres:throwaway@127.0.0.1:$PORT/postgres"
uv run ruff check .
uv run pyright src
uv run pytest -q --run-live
echo "==> check-live OK"
