# `mini-cloud-analytics`

Mixpanel-style **product analytics** — per-person, timestamped events with funnels and retention —
on the shared mini-cloud Postgres + Grafana. A different concern from `mini-cloud-obs`: `obs` asks
*"is the service healthy?"*; analytics asks *"did **this person** go upload → process → search →
chat, and where did they drop off?"*

The client mirrors PostHog's `capture` / `identify` / `alias`, so a maturing demo can flip
`MINI_ANALYTICS_BACKEND=posthog` and ship to real PostHog **by changing env, not code**.

```python
from mini_cloud.config import load_settings
from mini_cloud.db import make_pool, migrate
from mini_cloud.analytics import Analytics, migrations_path, run_funnel

settings = load_settings()
pool = make_pool(settings.require("analytics_dsn"))  # a SEPARATE db from DATABASE_URL
migrate(pool, migrations_path())  # this package ships its own schema

analytics = Analytics.from_settings(settings, source=pool)
analytics.capture("user-42", "document_uploaded", {"bytes": 1024})
analytics.identify("user-42", {"plan": "pro"})
funnel = run_funnel(pool, ["document_uploaded", "search_performed"], project=analytics.project)
analytics.close()  # flush + stop the background thread
```

## What you get

| Piece | Behaviour |
|---|---|
| **`capture()`** | never blocks the request path — events go into a bounded buffer a daemon thread flushes on size/interval and at shutdown. Buffer full ⇒ the event is dropped and `analytics_events_dropped_total` increments (honest backpressure, same as the real PostHog client). |
| **`identify()` / `alias()`** | low-frequency person-graph writes, straight to the sink. `alias` stitches an anonymous id to an identified one. |
| **Query-time identity** | the write path is a dumb append (`person_id` left NULL). Funnel/retention SQL collapses anonymous→identified by joining `analytics_person_aliases` at read time. |
| **`EventSink`** | `PostgresSink` (default) writes the event store; `PostHogSink` is the `[posthog]`-extra graduation seam (stubbed in v0). Chosen by `MINI_ANALYTICS_BACKEND`. |
| **Funnels & retention** | `run_funnel` / `run_retention` (+ the pure `funnel_sql` / `retention_sql` builders) resolve identity and compute conversion / weekly cohorts. |
| **Package-owned schema** | `migrations_path()` → the ordered `.sql` the consumer applies against `MINI_ANALYTICS_DSN` with `mini_cloud.db.migrate`. |

## Config

Canonical env (see `docs/env-and-ports.md`):

| Env | Meaning | Default |
|---|---|---|
| `MINI_ANALYTICS_DSN` | Postgres DSN to the shared `analytics` event store (separate DB, same `:15432`) | — |
| `MINI_ANALYTICS_BACKEND` | `postgres` \| `posthog` | `postgres` |
| `MINI_ANALYTICS_PROJECT` | tags events with the calling project | falls back to `APP_NAME` |

`posthog-python` is an optional `[posthog]` extra — the core imports clean (obs-style). Analytics is
**opt-in**: not every app needs it, so it's showcased in `examples/ref-showcase`, not added as an
8th scorecard gate.
