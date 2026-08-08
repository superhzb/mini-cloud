.PHONY: setup run test lint check

setup:
	uv sync --package mini-cloud-identity

run:
	@test -f .env || { echo "no .env — run: cp .env.example .env"; exit 1; }
	@set -a; . ./.env; set +a; exec uv run --package mini-cloud-identity mini-cloud-identity

test:
	uv run --package mini-cloud-identity pytest

lint:
	uv run --package mini-cloud-identity ruff check .
	uv run --package mini-cloud-identity pyright .

check: lint test
