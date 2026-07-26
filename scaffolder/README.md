# scaffolder — `mini`

The `mini` CLI: **`mini new`** (Phase 4) scaffolds + provisions an app in one command, and
**`mini score`** (Phase 4.6) scores any repo against the seven-metric scorecard. Stdlib-only — it
shells out to the infra stack's own tested scripts rather than pulling psycopg/boto3.

## `mini new <name> --type {fastapi|vite|node}`

One command does all of it (each side-effect best-effort and reported):

1. **Allocate ports** from the registry ranges (`19201–19299` API, `19101–19199` web), skipping the
   reserved MLX port and any already in `projects.json`.
2. **Render the template** (`../templates/<type>`) with the app's name/package/ports.
3. **Provision DB + bucket** via `infra/scripts/create-project.sh` — a per-project least-privilege
   role (the app never gets the admin creds used to make it).
4. **Write canonical `.env`** (real `DATABASE_URL`, `STORAGE_*`, `MINI_INFERENCE_URL`, …).
5. **Register the brbot-router route** (append to `projects.json`, idempotent on name).
6. **Provision a Grafana dashboard** (request rate, p95, logs — filtered to the app).
7. **Install deps** (`uv sync` / `npm install`) so a lockfile exists, then **`git init`**.

```bash
mini new demo-x --type fastapi
# → running, routed app with DB + bucket + logging + metrics, scoring 7/7

# useful flags: --no-provision --no-setup --no-git --path <dir>
```

Result: `demo-x` at `demo-x.brettbot.ca` once brbot-router picks up the route, **7/7 on the
scorecard out of the box**.

## `mini score [repo] [--min N]`

Scores a repo 0–7 against the scorecard, printing which metrics fail and why. Honest by
construction: a fresh `mini new` app is 7/7; an unmodified legacy repo scores lower. `--min` makes
it a CI gate (non-zero below the threshold). It only reads the target repo — never modifies it.

```bash
mini score .                 # score the current repo
mini score ../fr-hub-api --min 7
```

See [`../docs/adoption-guide.md`](../docs/adoption-guide.md) for the self-service adoption workflow
an existing repo's owner follows to converge on the standard.
