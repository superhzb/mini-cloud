# AGENTS.md — ref-fastapi

Machine-readable repo map (scorecard metric #5). An agent should be able to bootstrap, navigate,
change, and validate this repo from this file alone.

## What this is

The mini-cloud **reference FastAPI app**: a complete demo that uses every SDK package and carries
no bespoke SQLite/filesystem/inference code. It is the `fastapi` template seed and must stay 7/7 on
the scorecard.

## Bootstrap

```bash
make setup      # uv sync + copy .env.example -> .env
make check      # lint + typecheck + unit tests (no services needed)
```

## Task entrypoints (same names in every mini-cloud repo)

| Command | Does |
|---|---|
| `make setup` | install pinned deps, seed `.env` |
| `make run` | web server on canonical `PORT` (uvicorn) |
| `make worker` | background job worker (separate process) |
| `make migrate` | apply DB migrations |
| `make seed` | POST one demo note to a running server |
| `make test` | unit tests (no services) |
| `make lint` / `make fmt` | ruff check / autofix (shared config) |
| `make check` | full gate: lint + pyright + tests (non-zero on failure) |
| `make check-live` | full gate against an ephemeral throwaway Postgres (needs docker) |

## Layout

| Path | Contents |
|---|---|
| `src/ref_fastapi/app.py` | FastAPI app factory; `/healthz`, `/readyz`, `/notes`, `/queue/depth`, `/metrics` |
| `src/ref_fastapi/resources.py` | wires config→db/storage/inference into one `Resources` object |
| `src/ref_fastapi/tasks.py` | the `summarize` job handler (storage + inference; idempotent) |
| `src/ref_fastapi/worker.py` | the queue-draining worker process |
| `migrations/*.sql` | app schema (`NNNN_*.sql`, applied in order once each) |
| `tests/` | unit tests + a `live` end-to-end smoke test |
| `.env.example` | canonical env; copy to `.env` |

## Conventions

- **Config only through `mini_cloud.config`** — never hardcode service URLs. Canonical names:
  `DATABASE_URL`, `STORAGE_*`, `MINI_INFERENCE_URL`, `LOKI_URL`, `PORT`, `APP_ENV`.
- **Persistence via `mini_cloud.db`**, blobs via `mini_cloud.storage`, inference via
  `mini_cloud.inference`, logs/metrics via `mini_cloud.obs`. No bespoke replacements.
- **Job handlers must be idempotent** (queue is at-least-once).
- `/healthz` = liveness (never touches services); `/readyz` = dependency reachability.

## Where things go

- New HTTP route → `app.py` (add a pydantic model for the body).
- New background work → a handler in `tasks.py` + a queue name in `resources.py`; enqueue from a
  route; the worker loop is generic.
- New table → a new `migrations/NNNN_*.sql`.
