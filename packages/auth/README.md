# `mini-cloud-auth`

Thin **platform identity** for mini-cloud apps — the verifier half of Phase 6. The identity service
(`mini-cloud-identity`, in-repo at `services/identity/`) is the login authority: it takes a human through Google OAuth
and mints a short-lived, **asymmetrically signed** platform JWT. This package verifies that token and
enforces per-app authorization — it never talks to Google and holds **no secret**.

*Platform-wide identity, per-app authorization:* one token verifies at every app (a fixed
`aud: "mini-cloud"`); *who may use which app, at what role* rides in a `grants` claim (`{app: role}`).
`aud` proves only "a mini-cloud identity token" — all per-app authZ is the `grants[app]` check.

```python
from fastapi import Depends, FastAPI
from mini_cloud.auth import Principal
from mini_cloud.auth.fastapi import require_user   # needs mini-cloud-auth[fastapi]

app = FastAPI()

@app.get("/whoami")
def whoami(user: Principal = Depends(require_user(app="my-app", role="member"))):
    return {"sub": user.sub, "email": user.email, "role": user.role_for("my-app")}
```

That's the whole switch-on. Set three env vars (below) and the dependency verifies every request's
`Authorization: Bearer <jwt>` against the identity service's public keys.

## What you get

| Piece | Behaviour |
|---|---|
| `verify_token(token) -> Principal` | Fetches the identity service's JWKS (cached by `kid`, refetched on an unknown `kid` for rotation), validates the signature + `iss`/`aud`/`exp`/`sub`, and returns a typed `Principal(sub, email, grants)`. FastAPI-free. |
| `require_user(*, app=None, role=None)` | A FastAPI dependency: extracts the bearer token, verifies it, and enforces `grants[app]`. **401** no/invalid token · **403** valid token but missing app grant / role · **503** JWKS unreachable. |
| `Principal` | `sub`, `email`, `grants: {app: role}`, raw `claims`; plus `role_for(app)` / `is_authorized(app, role)`. |
| `TokenVerifier` | Build once, share (it caches JWKS). `from_settings()` / `from_config()` for the real network path; `from_jwks_set()` for offline tests or pinning keys. |
| `check_grant` / `ROLE_RANK` | The authorization gate and the coarse role ladder (`viewer < member < admin`; unknown roles need an exact match). |

## Roles

`require_user(role="member")` is satisfied by any grant of equal-or-higher rank in `ROLE_RANK`
(`viewer` < `member` < `admin`) — an `admin` passes a `member` gate. A role outside that ladder (an
app's bespoke role) must match **exactly**; for a custom hierarchy, pass `role=None` and branch on
`principal.role_for(app)` yourself.

## Configuration

| Env var | Meaning | Default |
|---|---|---|
| `MINI_AUTH_ISSUER` | Identity service base URL; the JWT `iss` | *(required)* |
| `MINI_AUTH_JWKS_URL` | JWKS endpoint | `${MINI_AUTH_ISSUER}/.well-known/jwks.json` |
| `MINI_AUTH_AUDIENCE` | Expected `aud` (the fixed platform audience — **not** per-app) | `mini-cloud` |

The verifier stays tiny on purpose (`config` + PyJWT + httpx — no `db`/`storage`) so any demo can
adopt it cheaply. The storage-backed grants store lives entirely in the identity service; an app only
ever sees the signed JWT.

## Testing

Unit tests are fully offline — a test keypair mints tokens and `TokenVerifier.from_jwks_set` verifies
them, so `pytest` needs no identity service. A `--run-live` test (with `MINI_AUTH_ISSUER` set) fetches
the real JWKS.

```bash
uv run --package mini-cloud-auth pytest packages/auth               # offline
uv run --package mini-cloud-auth pytest packages/auth --run-live    # + real JWKS
```
