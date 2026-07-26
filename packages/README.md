# packages — the mini-cloud SDK

Small, **independently-versioned** packages (Python + TS), not one monolith. Consumers pin the
versions they want, so a change to one package never forces every app to move.

**Status:** Phase 2 + 3 shipped. Each package is an independent distribution (`mini-cloud-<name>`)
providing the `mini_cloud.<name>` import via a PEP 420 namespace, with its own version, tests, and
README. Develop them together with `uv sync --all-packages` from the repo root.

| Package | Import | Responsibility | Phase | State |
|---|---|---|---|---|
| `config` | `mini_cloud.config` | load canonical env; single source of truth for service URLs/names (see `../docs/env-and-ports.md`) | 2 | ✅ |
| `db` | `mini_cloud.db` | Postgres connection, migrations, and a job-queue primitive (replaces the 4 bespoke SQLite stacks) | 2 | ✅ |
| `storage` | `mini_cloud.storage` | S3/MinIO client, per-project bucket | 2 | ✅ |
| `obs` | `mini_cloud.obs` | structured logging → Loki, metrics → Prometheus, correlation-ID propagation | 2 | ✅ |
| `inference` | `mini_cloud.inference` | thin OpenAI-client wrapper at `MINI_INFERENCE_URL` (folds in `hub-gateway` + `pkg-llm-backend`) | 3 | ✅ |
| `analytics` | `mini_cloud.analytics` | Mixpanel-style product analytics (capture/identify/alias, funnels, retention) on the shared `analytics` Postgres; PostHog-compatible | 7 | ✅ |
| `auth` | `mini_cloud.auth` | JWT verify helper | 6 | ▫️ deferred |

## Dependency graph

`config` sits at the bottom and depends on nothing. `db`, `storage`, `obs`, and `inference` each
depend only on `config` (never on each other), so they version independently — a change to
`inference` never touches `storage`. `analytics` is the one package that depends on another SDK
package (`db`, for the Postgres event sink) in addition to `config`, and soft-imports `obs`
correlation when present.

```text
config  ◀── db ◀── analytics
        ◀── storage
        ◀── obs
        ◀── inference
```

## Test

Per package (each has live tests gated behind `--run-live` + the relevant service env):

```bash
uv run --package mini-cloud-db pytest packages/db
uv run --package mini-cloud-db pytest packages/db --run-live   # needs a throwaway DATABASE_URL
```
