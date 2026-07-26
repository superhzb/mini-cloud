# How {{name}} scores 7/7

`mini score .` verifies this mechanically.

| # | Metric | How this repo passes |
|---|---|---|
| 1 | `bootstrap_self_sufficiency` | `make setup` (uv sync pinned + copy `.env.example`) |
| 2 | `task_entrypoints` | `Makefile` with canonical target names (`run`, `test`, `lint`, `check`, `migrate`, `worker`, `seed`) |
| 3 | `validation_harness` | `make check` (non-zero on failure) + `make check-live` on an ephemeral throwaway Postgres |
| 4 | `lint_format_gates` | ruff + pyright config extending the shared `../mini-cloud/tooling` base; `make lint` / `make fmt` |
| 5 | `agent_repo_map` | `AGENTS.md` |
| 6 | `structured_docs` | `README.md` + `docs/` + `.env.example` + `migrations/` |
| 7 | `observability_wired` | `obs.install(app, settings)` — JSON logs (+Loki), request metrics, correlation IDs, `/metrics`, on by default |
