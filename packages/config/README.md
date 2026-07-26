# `mini-cloud-config`

Load canonical mini-cloud env into a typed `Settings` object. The bottom of the SDK dependency
graph: **depends on nothing**, and every other package may depend on it.

```python
from mini_cloud.config import load_settings

settings = load_settings()  # reads process env, optionally seeded from ./.env
dsn = settings.require("database_url")  # clear error naming DATABASE_URL if unset
infer = settings.inference_url  # typed, may be None if the app has no inference
```

## Why

There is exactly **one canonical name per concept** — `MINI_INFERENCE_URL`, `DATABASE_URL`,
`STORAGE_ENDPOINT`, … — ending the historical three-URL inference split and the
`MLX_GATEWAY_URL` vs `MLX_PLATFORM_BASE_URL` divergence. See
[`../../docs/env-and-ports.md`](../../docs/env-and-ports.md) for the full registry; this package
loads exactly those keys.

Values are per-environment and swappable by design — that's what lets an app graduate to a VPS by
changing env, not code (`MINI_INFERENCE_URL` in particular flips to a cloud OpenAI-compatible
provider). So `config` treats them as runtime values, never hardcoded constants.

## API

| Symbol | Purpose |
|---|---|
| `load_settings(*, dotenv=".env", environ=None)` | Build `Settings` from process env (+ optional `.env`). Real env always wins over the file. |
| `Settings` | Frozen dataclass; one field per canonical env var. Optional fields are `\| None`. |
| `Settings.require(field)` | Return a value or raise `MissingConfigError` naming the env var to fix. |
| `load_dotenv(path, *, override=False)` | Minimal stdlib `.env` parser; missing file is a no-op. |
| `CANONICAL_ENV_KEYS` | Tuple of every canonical env-var name (used by `mini score`). |

Dependency-free (stdlib only) on purpose.
