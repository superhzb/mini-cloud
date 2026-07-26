# {{name}} (vite frontend)

A Vite + React app scaffolded by `mini new --type vite`.

> **Backend-complete until Phase 5.** The TypeScript SDK (`@mini-cloud/sdk` — typed API client,
> config, obs) ships in Phase 5. Until then this template wires config, routing, and the Grafana
> dashboard, but calls the API with a plain `fetch` through the `/api` proxy. It will reach 7/7 on
> the scorecard once the TS SDK lands. See `mini-cloud/docs/MINI_CLOUD_ARCHITECTURE.md` → Phase 5.

## Quick start

```bash
make setup       # npm install + .env
make run         # dev server on :{{web_port}} (proxies /api → :{{api_port}})
make check       # typecheck + lint + build
```

Repo map for agents: [`AGENTS.md`](AGENTS.md).
