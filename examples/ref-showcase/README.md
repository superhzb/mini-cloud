# ref-showcase — the mini-cloud "kitchen sink" reference

A small **Document Intelligence** service whose only job is to exercise *every public method of
every SDK package*, threaded into one believable domain. Where
[`ref-fastapi`](../ref-fastapi/) is the lean 7/7 template seed, `ref-showcase` is the exhaustive
surface that touches each SDK symbol first — so an SDK gap or signature drift breaks **here** before
it reaches downstream apps. It is the coverage/regression canary, and it must still score **7/7**.

> **Theme.** Upload documents → chunk & store blobs → a queue-driven pipeline embeds + summarizes →
> embeddings land in Postgres (`float8[]`, cosine computed in-app — no pgvector) → semantic search +
> per-document chat over the corpus.

## Quick start

```bash
make setup                                   # uv sync + seed .env
make -C ../../infra project NAME=ref-showcase  # provision this app's DB + bucket (needs infra up)
make -C ../../infra analytics-init             # provision the shared analytics DB (opt-in tour)
make run                                      # web server on PORT (default 19208)
make worker                                   # in another shell: the multi-queue worker
make seed                                     # deterministic corpus + analytics event stream
```

Open <http://localhost:19208/ui/> for the zero-build developer console, or use the API directly:

Then submit a document and watch the pipeline run:

```bash
curl -s -X POST localhost:19208/documents -H 'content-type: application/json' \
  -d '{"title":"Demo","text":"First paragraph.\nSecond paragraph.","tags":["demo"]}'
curl -s localhost:19208/documents            # list (paginated, filterable by ?tag= / ?status=)
curl -s localhost:19208/queue/stats          # depth per queue + dead-letter count
```

## Task entrypoints (same names as every mini-cloud repo)

| Command | What it does |
|---|---|
| `make setup` | install pinned deps + seed `.env` |
| `make run` / `make ui` | the same FastAPI server; the console is at `/ui/` |
| `make worker` | multi-queue background worker |
| `make migrate` | apply the ordered SQL migrations (also runs at startup) |
| `make seed` | generate + process the deterministic corpus with offline inference fallbacks |
| `make seed-live` | process the same corpus through the configured real gateway |
| `make test` | unit suite — **no services required** |
| `make check` | lint + pyright + unit tests (the full offline gate) |
| `make check-live` | the gate against an **ephemeral throwaway Postgres** (db + queue tours) |
| `make lint` / `make fmt` | shared ruff check / auto-fix |

## What it exercises (the tour, by package)

- **`config`** — canonical env, fail-fast `.require()`, `CANONICAL_ENV_KEYS`, and redacted
  `GET /debug/config`, including `MINI_INFERENCE_PROJECT`.
- **`db` (relational)** — `make_pool`, `transaction()` for atomic document+chunks+tags writes,
  three ordered migrations, real joins with pagination + filtering (`domain.py`).
- **`db` (queue)** — three fan-out queues (`ingest` → `embed` + `summarize`) plus `long`
  (heartbeat via `extend`) and `poison` (fail → backoff → dead-letter → **`requeue_dead_letter`**,
  a method added to the SDK for exactly this need). See `pipeline.py` / `worker.py`.
- **`storage`** — byte + stream uploads, listing, existence/download/delete, bucket lifecycle, and
  presigned GET/PUT URLs under namespaced prefixes.
- **`inference`** — embeddings/search, chat + multi-turn chat, models, and SSE streaming, with
  deterministic **offline fallbacks** for the pipeline and search.
- **`obs`** — `install()` plus three custom business collectors, a provisioned Grafana dashboard,
  and correlation IDs carried **inside job payloads** and re-bound in the worker.
- **`analytics`** — a real 4-step product funnel (`document_uploaded` → `document_processed` →
  `search_performed` → `chat_started`) with `identify`/`alias`, `capture`/funnel/retention/events
  tour endpoints, query-time identity resolution, and a Postgres-datasource Grafana dashboard. Opt-in
  on a **separate** `analytics` DB (`MINI_ANALYTICS_DSN`); `make -C ../../infra analytics-init`
  provisions it. See `analytics_tour.py`.

See [`docs/service-tour.md`](docs/service-tour.md) for the full endpoint/code/test walkthrough. The
AST-resolved coverage gate includes the non-root `mini_cloud.obs.asgi` public surface.

## Web console

The developer/operator workbench at `/ui/` drives the complete tour from one same-origin page:
service readiness, bounded sample generation, documents, object storage, queues, debug snapshots,
fallback search, live inference, and optional analytics. It is plain packaged HTML/CSS/JavaScript;
there is no Node runtime, build step, CDN, CORS setup, or frontend server.

The **Seed samples** control calls `POST /showcase/seed?count=N` (`1..12`, default `6`). It always
uses deterministic fallback inference and synchronously drains only `ingest`, `embed`, and
`summarize`, so results are ready to inspect when the response arrives. It is an unauthenticated
local-reference convenience, not a production administration API. The concurrency guard is local
to one Uvicorn process.

The console detects unavailable services independently. Missing inference disables chat/models/
streaming but leaves fallback search active; missing analytics shows `MINI_ANALYTICS_DSN` guidance;
other sections remain usable according to the readiness matrix.

## Build status

Steps 1–7 are implemented. See [`docs/build-status.md`](docs/build-status.md) for validation state.
