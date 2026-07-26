# How ref-fastapi scores 7/7

The seven-metric scorecard (see `../../../docs/MINI_CLOUD_ARCHITECTURE.md` → *Scorecard*), and how
this app satisfies each. `mini score .` verifies it mechanically.

| # | Metric | How this repo passes |
|---|---|---|
| 1 | `bootstrap_self_sufficiency` | `make setup` (uv sync pinned via `uv.lock` + copy `.env.example`); one documented command. |
| 2 | `task_entrypoints` | `Makefile` with the canonical target names (`run`, `test`, `lint`, `check`, `migrate`, `worker`, `seed`) shared across every repo. |
| 3 | `validation_harness` | `make check` (lint + pyright + tests, non-zero on failure) and `make check-live` against an **ephemeral throwaway Postgres** (`scripts/check-live.sh`), not the always-on stack. |
| 4 | `lint_format_gates` | `[tool.ruff] extend = "../../tooling/ruff-base.toml"` + `pyrightconfig.json` extending the shared base. `make lint` / `make fmt`. |
| 5 | `agent_repo_map` | `AGENTS.md` — machine-readable map of entrypoints, layout, and conventions. |
| 6 | `structured_docs` | `README.md` + this `docs/` + `.env.example` documenting canonical env; migrations in `migrations/`. |
| 7 | `observability_wired` | `obs.install(app, settings)` wires JSON logs (+Loki), request metrics, correlation IDs, and `/metrics` — on by default, no flag. |
