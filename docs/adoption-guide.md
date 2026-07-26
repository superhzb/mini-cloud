# Adoption guide — bringing an existing repo onto the standard

This is the **self-service** workflow a repo owner follows to converge an existing project onto the
mini-cloud standard. mini-cloud provides the SDK, the templates, the scorecard, and this guide —
**it does not run this on anyone's repo**. Each step is independently shippable; stop whenever the
remaining value isn't worth it.

> Nothing here is mandatory or automatic. New apps get the standard for free via `mini new`; this
> guide is only for repos that already exist and choose to adopt.

## 0. Baseline

```bash
mini score .
```

You get a 0–7 score and a per-metric ✓/✗ with reasons. That's your starting point and your target
(7/7). Re-run it after every step below.

## 1. Wire config (canonical env names)

Adopt `mini-cloud-config` (or just the documented env names it defines — see
[`env-and-ports.md`](env-and-ports.md)). Replace every hardcoded `127.0.0.1:8933` / `:9000` /
`:5900` and every divergent name (`MLX_GATEWAY_URL`, `MLX_PLATFORM_BASE_URL`, `API_PORT`, …) with
the one canonical name for each concept, loaded in one place.

```python
from mini_cloud.config import load_settings
settings = load_settings()
dsn = settings.require("database_url")
```

Delete the divergent names as you go. This step alone unlocks graduation-readiness later.

## 2. Adopt incrementally (one pinned package at a time)

Pull packages in the low-risk order, **pinning each version** and re-scoring after each. Adopting
one does not require the rest (per the decoupling model).

1. **`obs`** — `obs.install(app, settings)` gives you JSON logs → Loki, request metrics →
   Prometheus, correlation IDs, and `/metrics`, visible in the shared Grafana. (Scorecard #7.)
2. **`storage`** — replace filesystem-as-object-store with a per-project bucket
   (`Storage.from_settings`).
3. **`db`** — move relational state + any bespoke SQLite/job-queue stack onto shared Postgres and
   the SDK job queue (`JobQueue`; at-least-once — make handlers idempotent).
4. **`inference`** — fold a hand-rolled inference client into `InferenceClient` at the one canonical
   gateway URL.
5. **`analytics`** *(optional)* — add product analytics (funnels/retention) only if a PM/growth
   question calls for it. It's a **distinct concern from `obs`** (per-person events, not aggregated
   health) on a **separate** `analytics` DB: `make -C ../mini-cloud/infra analytics-init`, set
   `MINI_ANALYTICS_DSN`, `migrate(analytics_pool, mini_cloud.analytics.migrations_path())`, then
   `Analytics.from_settings(settings, source=analytics_pool)` and `capture`/`identify`/`alias` at
   intentional product moments. Not scored — opt-in. `MINI_ANALYTICS_BACKEND=posthog` is the env-only
   seam to real PostHog later.

Provision the repo's DB + bucket once (if not already): `make -C ../mini-cloud/infra project
NAME=<repo>`.

## 3. Meet the repo surface

Add the standard scaffolding the scorecard checks for — copy from the `fastapi` template or
`examples/ref-fastapi`:

- **Task entrypoints** — a `Makefile` with the canonical target names (`setup`, `run`, `test`,
  `lint`, `check`, plus `migrate`/`worker` where relevant). (Scorecard #2.)
- **Validation harness** — `make check` (lint + typecheck + tests, non-zero on failure) and a
  `check-live` against an **ephemeral throwaway Postgres**, not the always-on stack. (#3.)
- **Lint/format gates** — reference the shared tooling base instead of copying it:
  `[tool.ruff] extend = "…/mini-cloud/tooling/ruff-base.toml"` and a `pyrightconfig.json` that
  extends `pyright-base.json`. (#4.)
- **Agent repo map** — an `AGENTS.md` stating where things live, the entrypoints, the conventions.
  (#5.)
- **Structured docs** — `README.md` + `docs/` + `.env.example`. (#6.)
- **Bootstrap** — pinned lockfile + `.env.example` + a one-command `make setup`. (#1.)

## 4. Re-score to 7/7

```bash
mini score . --min 7
```

Record the result. **Graduation-readiness falls out for free** once config + SDK are in place: to
move to a VPS you repoint env (the same compose stack there, or managed Postgres/S3) and DNS —
no application rewrite, because the app only ever spoke Postgres/S3/OpenAI/OIDC over config.

## Notes

- Cleaning up already-committed `.env` files (in `mlx-audio`, `srt-flow`, `fr-hub-api`) is part of
  this repo's own adoption, not something mini-cloud does. The templates ship the
  `.env.example` + untracked `.env` convention; adopt it here too.
- Prod/staging directory copies collapse to one env-switched deployable (`APP_ENV`) — the templates
  model the pattern.
