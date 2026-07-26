# ref-fastapi — the mini-cloud reference app

A complete, small FastAPI service that exercises **every** SDK package end-to-end and carries no
bespoke SQLite writer, filesystem-as-store, or hand-rolled inference client. It is:

1. the **SDK proof** — if the platform works, this runs on shared Postgres + MinIO through the SDK,
   emits logs/metrics to Grafana, and uses the SDK job queue;
2. the **seed** for the `fastapi` template; and
3. the **regression guard** — it must hold **7/7 on the scorecard**.

## Demo flow (notes → summary)

```text
POST /notes {text}
   └─ storage.put_bytes(note_key, text)          # mini_cloud.storage
   └─ queue.enqueue("summarize", {note_key})     # mini_cloud.db (job queue)
        │
   worker: dequeue → load text → summarise (inference, or trivial fallback) → store summary
        │                                          # mini_cloud.inference
GET /notes/{id}/summary  → the produced summary   (404 until the worker finishes)
```

Every request is JSON-logged with a correlation ID and counted in Prometheus (`mini_cloud.obs`);
`/readyz` reports DB + storage reachability.

## Run it (with the infra stack up)

```bash
# 1. bring up infra and provision this app's DB + bucket (from the repo root):
make -C ../../infra up
make -C ../../infra project NAME=ref-fastapi

# 2. this app:
make setup            # uv sync + .env
make run              # web server on PORT (19204)
make worker           # in another shell: the job worker
make seed             # POST a demo note; then GET /notes/<id>/summary
```

Open Grafana (`http://localhost:13000`) to see this app's logs and request metrics.

## Validate

```bash
make check            # lint + typecheck + unit tests — no services needed
make check-live       # the same, plus queue/storage tests on an ephemeral throwaway Postgres
```

## Scorecard

This app is built to score **7/7**. See [`docs/scorecard.md`](docs/scorecard.md) for how each
metric is satisfied. `mini score .` (Phase 4.6) checks it mechanically.

More docs: [`docs/`](docs/) · repo map for agents: [`AGENTS.md`](AGENTS.md).
