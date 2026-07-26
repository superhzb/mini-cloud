# {{name}} (vite) — standard mini-cloud task entrypoints (npm-backed).
.PHONY: help setup run build test check lint fmt

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n", $$1, $$2}'

setup: ## Install pinned deps + seed .env
	npm ci || npm install
	@test -f .env || cp .env.example .env

run: ## Dev server on the canonical web port
	npm run dev

build: ## Production build
	npm run build

test: ## (no unit tests yet — typecheck stands in)
	npm run typecheck

lint: ## Lint
	npm run lint

fmt: ## Autofix lint
	npm run lint -- --fix

check: ## Full gate: typecheck + lint + build
	npm run typecheck
	npm run lint
	npm run build
