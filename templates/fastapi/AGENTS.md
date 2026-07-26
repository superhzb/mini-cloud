# AGENTS.md — {{name}}

Machine-readable repo map (scorecard #5). Bootstrap, navigate, change, and validate from here.

## What this is

A mini-cloud FastAPI app (`mini new --type fastapi`). Uses the SDK for config, DB + job queue,
storage, observability, and inference — no bespoke replacements. Scores 7/7 on the scorecard.

## Bootstrap

```bash
make setup      # uv sync + copy .env.example -> .env
make check      # lint + typecheck + unit tests (no services needed)
```

## Task entrypoints (same names in every mini-cloud repo)

| Command | Does |
|---|---|
| `make setup` | install pinned deps, seed `.env` |
| `make run` | web server on canonical `PORT` ({{api_port}}) |
| `make worker` | background job worker (separate process) |
| `make migrate` | apply DB migrations |
| `make seed` | POST one demo note to a running server |
| `make test` | unit tests (no services) |
| `make lint` / `make fmt` | ruff check / autofix (shared config) |
| `make check` | full gate: lint + pyright + tests |
| `make check-live` | full gate against an ephemeral throwaway Postgres |

## Layout

| Path | Contents |
|---|---|
| `src/{{package}}/app.py` | FastAPI app; `/healthz`, `/readyz`, `/notes`, `/queue/depth`, `/metrics` |
| `src/{{package}}/resources.py` | wires config→db/storage/inference into one `Resources` |
| `src/{{package}}/tasks.py` | the background job handler (idempotent) |
| `src/{{package}}/worker.py` | the queue-draining worker |
| `migrations/*.sql` | app schema (`NNNN_*.sql`) |
| `.env.example` | canonical env |

## Conventions

- Config only through `mini_cloud.config`; canonical names only.
- Persistence via `mini_cloud.db`, blobs via `mini_cloud.storage`, inference via
  `mini_cloud.inference`, logs/metrics via `mini_cloud.obs`.
- Job handlers must be idempotent (queue is at-least-once).
- `/healthz` = liveness; `/readyz` = dependency reachability.

## Where things go

- New route → `app.py` (+ a pydantic model). New background work → a handler in `tasks.py`,
  enqueue from a route. New table → a new `migrations/NNNN_*.sql`.
