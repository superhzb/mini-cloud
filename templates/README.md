# templates — app skeletons

App skeletons the scaffolder (`mini new --type …`) stamps out. Files use `{{var}}` placeholders
substituted by the renderer; a filename ending `.tmpl` drops that suffix on output.

| Template | Status | Notes |
|---|---|---|
| `fastapi` | ✅ full 7/7 | The proven path — mirrors `examples/ref-fastapi`. Links the whole Python SDK (config · db + queue · storage · obs · inference), `/healthz` + `/readyz`, a worker, migrations, and the standard Makefile/AGENTS/docs. Scores 7/7 out of the box. |
| `vite` | ◑ backend-complete | Vite + React. Wires config, `/api` routing, and the Grafana dashboard, but calls the API with plain `fetch` — the typed client + obs arrive with the **Phase-5 TS SDK**, so it can't hit 7/7 yet (by design). |
| `node` | ◑ minimal | Zero-dependency Node HTTP service with `/healthz`, `/readyz`, `/metrics`. Richer DB/storage/obs come with the Phase-5 TS SDK. |

## Template variables

| Variable | Meaning | Example |
|---|---|---|
| `{{name}}` | app name (`[a-z][a-z0-9-]*`) | `demo-x` |
| `{{package}}` | Python module name (`-`→`_`) | `demo_x` |
| `{{description}}` | one-line description | `A mini-cloud fastapi app.` |
| `{{api_port}}` | assigned API port | `19201` |
| `{{web_port}}` | assigned web port (vite) | `19101` |
| `{{sdk_version}}` | SDK version specifier | `>=0.1.0` |

Generated apps are **siblings** of `mini-cloud/`, so their `pyproject.toml` resolves the SDK by
path (`../mini-cloud/packages/*`) and their tooling config extends `../mini-cloud/tooling/*` in dev.
On graduation, publish the SDK and pin versions instead.

## Adding a template

Drop a new directory here and add an entry to `TEMPLATE_META` in
`scaffolder/src/mini_scaffolder/new.py` (whether it needs a web port, and the brbot-router spawn
command). `mini new --type <dir>` then works.
