# {{name}}

A mini-cloud FastAPI app, scaffolded by `mini new`. Runs on shared Postgres + MinIO through the
SDK, emits logs/metrics to the shared Grafana, and uses the SDK job queue — with no bespoke
SQLite writer, filesystem-as-store, or hand-rolled inference client.

## Quick start

```bash
make setup            # uv sync + .env
make run              # web server on :{{api_port}}
make worker           # in another shell: the background job worker
make seed             # POST a demo note; then GET /notes/<id>/summary
```

Grafana (`http://localhost:13000`) shows this app's logs and request metrics on the auto-provisioned
`app · {{name}}` dashboard.

## Demo flow (replace with your own)

```text
POST /notes {text}  → store text in the bucket + enqueue a job
   worker           → load text → summarise (inference or fallback) → store summary
GET /notes/{id}/summary → the summary (404 until the worker finishes)
```

## Validate

```bash
make check            # lint + typecheck + unit tests — no services needed
make check-live       # the same, on an ephemeral throwaway Postgres
```

Repo map for agents: [`AGENTS.md`](AGENTS.md). Scorecard: [`docs/scorecard.md`](docs/scorecard.md).
