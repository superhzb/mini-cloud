# {{name}} (node)

A minimal, zero-dependency Node HTTP service scaffolded by `mini new --type node`. Reads canonical
env (`PORT`, `APP_NAME`) and exposes `/healthz`, `/readyz`, `/metrics`.

```bash
make setup     # npm install + .env
make run       # server on :{{api_port}}
make check     # lint + test
```

The Phase-5 TypeScript SDK (`@mini-cloud/sdk`) adds config, a typed client, DB/storage, and richer
observability — wire it in when available. Repo map: [`AGENTS.md`](AGENTS.md).
