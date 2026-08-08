"""The FastAPI application: OAuth login, JWKS, the mint path, ``/userinfo``, and the dev grant.

``create_app`` does all the boot wiring — resolve the (fail-closed) dev-login switch, load or mint
the signing key, open the authorization store and apply its migrations, seed the dev admin, and
build the verifier + Google client. Components are injectable so the offline tests can hand in an
``InMemoryStore`` and a fixed signing key with no Postgres and no env.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from mini_cloud.auth import Principal, TokenInvalidError, TokenVerifier
from mini_cloud.config import load_settings
from mini_cloud.obs import get_logger
from pydantic import BaseModel

from .config import IdentitySettings
from .devlogin import (
    DevAdmin,
    password_request_is_local,
    resolve_dev_login,
    seed_dev_admin,
)
from .google import GoogleAuthError, GoogleOAuth
from .keys import SigningKey, load_signing_key
from .store import GrantsStore, InMemoryStore, PostgresStore
from .tokens import mint_access_token

if TYPE_CHECKING:
    from mini_cloud.db import ConnSource

_log = get_logger("mini_cloud_identity")
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class DevTokenIn(BaseModel):
    username: str
    password: str


def create_app(
    settings: IdentitySettings | None = None,
    *,
    store: GrantsStore | None = None,
    signing: SigningKey | None = None,
) -> FastAPI:
    """Build the identity service. See module docstring for the boot sequence."""
    cfg = settings or IdentitySettings.from_env()

    # Fail closed FIRST: refuse to boot with the developer password login outside APP_ENV=dev.
    dev_login = resolve_dev_login(
        enabled=cfg.dev_login_enabled,
        issuer=cfg.issuer,
        app_env=cfg.app_env,
        force=cfg.dev_login_force,
    )

    signing = signing or load_signing_key(
        pem=cfg.signing_key_pem, kid=cfg.signing_kid, algorithm=cfg.signing_algorithm
    )
    if signing.ephemeral:
        _log.warning(
            "using an EPHEMERAL signing key — tokens are invalidated on restart; mount "
            "MINI_AUTH_SIGNING_KEY[_FILE] for a stable key",
            extra={"kid": signing.kid},
        )

    pool: ConnSource | None = None
    if store is None:
        store, pool = _open_store(cfg)

    if dev_login:
        seed_dev_admin(
            store, DevAdmin(username=cfg.dev_admin_user, password=cfg.dev_admin_password)
        )
        _log.warning(
            "PASSWORD LOGIN ENABLED — POST /login/password mints platform tokens on local "
            "network hosts; %r/**** is a platform admin. Disable with "
            "MINI_AUTH_PASSWORD_LOGIN=0 when no longer needed.",
            cfg.dev_admin_user,
        )

    verifier = TokenVerifier.from_jwks_set(signing.jwks(), issuer=cfg.issuer, audience=cfg.audience)
    google = _build_google(cfg)

    app = FastAPI(title="mini-cloud-identity", version="0.1.0")
    _install_obs(app)
    app.state.cfg = cfg
    app.state.signing = signing
    app.state.store = store
    app.state.pool = pool
    app.state.dev_login = dev_login

    def mint_for(
        *, email: str, sub: str, name: str | None = None, picture: str | None = None
    ) -> str:
        """The one mint path: cache profile, read grants, sign. Shared by both login methods."""
        store.upsert_user(sub=sub, email=email, name=name, picture=picture)
        grants = store.grants_for(email)
        return mint_access_token(
            signing,
            issuer=cfg.issuer,
            audience=cfg.audience,
            sub=sub,
            email=email,
            grants=grants,
            ttl_seconds=cfg.access_ttl,
        )

    # --- probes ---------------------------------------------------------------------
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        checks: dict[str, bool] = {}
        if pool is not None:
            from mini_cloud.db import acquire

            try:
                with acquire(pool) as conn:
                    conn.execute("SELECT 1")
                checks["store"] = True
            except Exception:  # noqa: BLE001 — a failed probe is a False check, not a 500
                checks["store"] = False
        else:
            checks["store"] = True  # in-memory store is always reachable
        ready = all(checks.values())
        body = {
            "ready": ready,
            "checks": checks,
            "signing_ephemeral": signing.ephemeral,
            "dev_login": dev_login,
            "google_configured": google is not None,
        }
        return JSONResponse(body, status_code=200 if ready else 503)

    # --- JWKS: the trust anchor every verifier reads --------------------------------
    @app.get("/.well-known/jwks.json")
    def jwks() -> dict[str, object]:
        return signing.jwks()

    # --- Google OAuth ---------------------------------------------------------------
    @app.get("/login")
    def login(request: Request) -> RedirectResponse:
        if google is None:
            raise HTTPException(503, "Google OAuth not configured (set GOOGLE_CLIENT_ID/SECRET)")
        return RedirectResponse(
            google.authorization_url(return_to=request.query_params.get("return_to")),
            status_code=307,
        )

    @app.get("/callback", response_model=None)
    def callback(request: Request) -> JSONResponse | RedirectResponse:
        if google is None:
            raise HTTPException(503, "Google OAuth not configured")
        params = request.query_params
        if params.get("error"):
            raise HTTPException(400, f"Google returned an error: {params.get('error')}")
        code, state = params.get("code"), params.get("state")
        if not code or not state:
            raise HTTPException(400, "missing code/state on the OAuth callback")
        try:
            identity = google.exchange(code=code, state=state)
        except GoogleAuthError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not identity.email:
            raise HTTPException(400, "Google account has no email; cannot resolve grants")
        token = mint_for(
            email=identity.email, sub=identity.sub, name=identity.name, picture=identity.picture
        )
        if cfg.post_login_redirect:
            target = f"{cfg.post_login_redirect}#access_token={token}&token_type=bearer"
            return RedirectResponse(target, status_code=307)
        return JSONResponse(_token_body(token, cfg.access_ttl))

    # --- convenience: decode the caller's own token ---------------------------------
    @app.get("/userinfo")
    def userinfo(request: Request) -> dict[str, object]:
        principal = _require_bearer(request, verifier)
        return {"sub": principal.sub, "email": principal.email, "grants": principal.grants}

    # --- username/password login (mints the SAME token as Google) --------------------
    def password_token(body: DevTokenIn, request: Request) -> JSONResponse:
        if not dev_login:
            raise HTTPException(404, "password login is disabled")
        forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
        request_host = forwarded_host or request.url.hostname
        if not password_request_is_local(request_host):
            raise HTTPException(
                403, "developer password login is available on the local network only"
            )
        from .passwords import verify_password

        user = store.get_dev_user(body.username)
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "invalid dev credentials")
        token = mint_for(email=user.email, sub=f"dev|{user.username}")
        return JSONResponse(_token_body(token, cfg.access_ttl))

    app.post("/login/password", name="password_login")(password_token)
    # Backwards-compatible endpoint used by existing scripts and test tokens.
    app.post("/dev/token", name="dev_token", include_in_schema=False)(password_token)

    @app.get("/")
    def root() -> dict[str, object]:
        return {
            "service": "mini-cloud-identity",
            "issuer": cfg.issuer,
            "jwks": "/.well-known/jwks.json",
            "login_methods": {
                "password": "/login/password" if dev_login else None,
                "google": "/login" if google is not None else None,
            },
            "dev_login_compat": "/dev/token" if dev_login else None,
        }

    return app


def _open_store(cfg: IdentitySettings) -> tuple[GrantsStore, ConnSource | None]:
    """Postgres store (+ boot migrations) when a DSN is set; else an in-memory dev fallback."""
    if not cfg.database_url:
        _log.warning(
            "no IDENTITY_DATABASE_URL — using an EPHEMERAL in-memory grants store (dev only). "
            "Run `make -C infra identity-init` and set IDENTITY_DATABASE_URL for a real store."
        )
        return InMemoryStore(), None
    from mini_cloud.db import make_pool, migrate

    pool = make_pool(cfg.database_url)
    applied = migrate(pool, _MIGRATIONS_DIR)  # service owns + applies its own schema on boot
    if applied:
        _log.info("applied identity migrations", extra={"versions": applied})
    return PostgresStore(pool), pool


def _build_google(cfg: IdentitySettings) -> GoogleOAuth | None:
    if not (cfg.google_client_id and cfg.google_client_secret and cfg.google_redirect_uri):
        return None
    state_secret = cfg.state_secret or secrets.token_urlsafe(32)  # ephemeral is fine (same process)
    return GoogleOAuth(
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
        redirect_uri=cfg.google_redirect_uri,
        state_secret=state_secret,
    )


def _install_obs(app: FastAPI) -> None:
    """Best-effort observability (logging + metrics + /metrics), on by default like every app."""
    try:
        from mini_cloud.obs.asgi import install

        install(app, load_settings())
    except Exception as exc:  # noqa: BLE001 — obs must never block the service from booting
        _log.warning("obs install skipped", extra={"error": str(exc)})


def _require_bearer(request: Request, verifier: TokenVerifier) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        return verifier.verify_token(value.strip())
    except TokenInvalidError as exc:
        raise HTTPException(
            401, f"invalid token: {exc}", headers={"WWW-Authenticate": "Bearer"}
        ) from exc


def _token_body(token: str, ttl: int) -> dict[str, object]:
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl}
