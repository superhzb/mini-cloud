"""mini_cloud.config — load canonical env; the single source of truth for service URLs/names.

This package exists so no app ever hardcodes ``127.0.0.1:8933`` / ``:9000`` / ``:5900`` again.
There is exactly one canonical *name* per concept (``MINI_INFERENCE_URL``, ``DATABASE_URL``,
``STORAGE_ENDPOINT`` …); values are per-environment and swappable, which is what lets an app
graduate to a VPS by changing env, not code. See ``docs/env-and-ports.md`` for the registry.

Usage::

    from mini_cloud.config import load_settings

    settings = load_settings()          # reads process env (+ optional .env)
    dsn = settings.database_url          # KeyError-free typed access
    infer = settings.inference_url

The loader is intentionally dependency-free (stdlib only) so ``config`` sits at the bottom of
the SDK dependency graph: every other package (``db``, ``storage``, ``obs``, ``inference``) may
depend on it, and it depends on nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

__version__ = "0.1.0"

__all__ = [
    "Settings",
    "load_settings",
    "load_dotenv",
    "MissingConfigError",
    "CANONICAL_ENV_KEYS",
]

LogLevel = Literal["debug", "info", "warn", "error"]
AppEnv = Literal["dev", "staging", "prod"]
AnalyticsBackend = Literal["postgres", "posthog"]


class MissingConfigError(RuntimeError):
    """Raised when a required canonical env var is absent and has no safe default."""


def load_dotenv(path: str | os.PathLike[str] = ".env", *, override: bool = False) -> dict[str, str]:
    """Minimal ``.env`` loader (no third-party dependency).

    Parses ``KEY=value`` lines, ignores blanks and ``#`` comments, strips one layer of matching
    quotes, and sets each key into :data:`os.environ` unless already present (``override=False``).
    Returns the parsed mapping. Missing file is a no-op (returns ``{}``) — a deployed app reads
    real process env, and only local dev seeds from a file.
    """
    p = Path(path)
    parsed: dict[str, str] = {}
    if not p.is_file():
        return parsed
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        parsed[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    """Typed view over the canonical mini-cloud environment.

    Every field maps to exactly one env var from ``docs/env-and-ports.md``. Fields that are
    optional in a given app (e.g. an app with no inference or no storage) are typed ``| None``
    and default to ``None`` / a loopback dev default; access a required-but-absent one through
    :meth:`require` to get a clear error instead of a downstream ``None`` surprise.
    """

    # --- inference -----------------------------------------------------------------
    inference_url: str | None = None  # MINI_INFERENCE_URL
    inference_project: str | None = None  # MINI_INFERENCE_PROJECT (identifies caller to gateway)
    # --- database ------------------------------------------------------------------
    database_url: str | None = None  # DATABASE_URL
    # --- object storage ------------------------------------------------------------
    storage_endpoint: str | None = None  # STORAGE_ENDPOINT
    storage_access_key: str | None = None  # STORAGE_ACCESS_KEY
    storage_secret_key: str | None = None  # STORAGE_SECRET_KEY
    storage_bucket: str | None = None  # STORAGE_BUCKET
    storage_region: str = "us-east-1"  # STORAGE_REGION
    # --- observability -------------------------------------------------------------
    loki_url: str | None = None  # LOKI_URL
    prometheus_pushgateway_url: str | None = None  # PROMETHEUS_PUSHGATEWAY_URL
    # --- product analytics ---------------------------------------------------------
    analytics_dsn: str | None = None  # MINI_ANALYTICS_DSN (shared analytics event store)
    analytics_backend: AnalyticsBackend = "postgres"  # MINI_ANALYTICS_BACKEND
    analytics_project: str | None = None  # MINI_ANALYTICS_PROJECT (defaults to APP_NAME at client)
    # --- identity ------------------------------------------------------------------
    auth_issuer: str | None = None  # MINI_AUTH_ISSUER (identity service URL; the JWT `iss`)
    auth_jwks_url: str | None = None  # MINI_AUTH_JWKS_URL (default: ${issuer}/.well-known/jwks)
    auth_audience: str = "mini-cloud"  # MINI_AUTH_AUDIENCE (fixed platform aud; authZ is `grants`)
    # --- misc ----------------------------------------------------------------------
    hf_token: str | None = None  # HF_TOKEN
    port: int | None = None  # PORT
    log_level: LogLevel = "info"  # LOG_LEVEL
    app_env: AppEnv = "dev"  # APP_ENV
    app_name: str | None = None  # APP_NAME (labels logs/metrics; scaffolder sets it)

    def require(self, field_name: str) -> str:
        """Return the value of ``field_name`` or raise :class:`MissingConfigError`.

        Use for values an app genuinely cannot run without, so misconfiguration fails fast at
        startup with the env-var name to fix rather than a ``NoneType`` error deep in a request.
        """
        value = getattr(self, field_name)
        if value is None or value == "":
            env_name = _FIELD_TO_ENV.get(field_name, field_name.upper())
            raise MissingConfigError(
                f"required setting '{field_name}' is unset — set env var {env_name} "
                f"(see docs/env-and-ports.md)"
            )
        return str(value)


# Field name -> canonical env var. Keeps error messages and CANONICAL_ENV_KEYS in one place.
_FIELD_TO_ENV: dict[str, str] = {
    "inference_url": "MINI_INFERENCE_URL",
    "inference_project": "MINI_INFERENCE_PROJECT",
    "database_url": "DATABASE_URL",
    "storage_endpoint": "STORAGE_ENDPOINT",
    "storage_access_key": "STORAGE_ACCESS_KEY",
    "storage_secret_key": "STORAGE_SECRET_KEY",
    "storage_bucket": "STORAGE_BUCKET",
    "storage_region": "STORAGE_REGION",
    "loki_url": "LOKI_URL",
    "prometheus_pushgateway_url": "PROMETHEUS_PUSHGATEWAY_URL",
    "analytics_dsn": "MINI_ANALYTICS_DSN",
    "analytics_backend": "MINI_ANALYTICS_BACKEND",
    "analytics_project": "MINI_ANALYTICS_PROJECT",
    "auth_issuer": "MINI_AUTH_ISSUER",
    "auth_jwks_url": "MINI_AUTH_JWKS_URL",
    "auth_audience": "MINI_AUTH_AUDIENCE",
    "hf_token": "HF_TOKEN",
    "port": "PORT",
    "log_level": "LOG_LEVEL",
    "app_env": "APP_ENV",
    "app_name": "APP_NAME",
}

CANONICAL_ENV_KEYS: tuple[str, ...] = tuple(_FIELD_TO_ENV.values())

_VALID_LOG_LEVELS = ("debug", "info", "warn", "error")
_VALID_APP_ENVS = ("dev", "staging", "prod")
_VALID_ANALYTICS_BACKENDS = ("postgres", "posthog")


def load_settings(
    *,
    dotenv: str | os.PathLike[str] | None = ".env",
    environ: dict[str, str] | None = None,
) -> Settings:
    """Build a :class:`Settings` from the environment.

    Order of precedence: real process env wins over ``.env`` file values (``load_dotenv`` never
    overrides an already-set key). Pass ``dotenv=None`` to skip file loading entirely (e.g. in
    tests or a container that injects env directly), or ``environ=`` to load from an explicit
    mapping instead of :data:`os.environ` (used by the test suite and ``mini score``).
    """
    if environ is None:
        if dotenv is not None:
            load_dotenv(dotenv)
        environ = dict(os.environ)

    def get(field_name: str) -> str | None:
        raw = environ.get(_FIELD_TO_ENV[field_name])
        return raw if raw not in (None, "") else None

    log_level = (get("log_level") or "info").lower()
    if log_level not in _VALID_LOG_LEVELS:
        raise MissingConfigError(
            f"LOG_LEVEL={log_level!r} invalid; expected one of {_VALID_LOG_LEVELS}"
        )
    app_env = (get("app_env") or "dev").lower()
    if app_env not in _VALID_APP_ENVS:
        raise MissingConfigError(f"APP_ENV={app_env!r} invalid; expected one of {_VALID_APP_ENVS}")
    analytics_backend = (get("analytics_backend") or "postgres").lower()
    if analytics_backend not in _VALID_ANALYTICS_BACKENDS:
        raise MissingConfigError(
            f"MINI_ANALYTICS_BACKEND={analytics_backend!r} invalid; "
            f"expected one of {_VALID_ANALYTICS_BACKENDS}"
        )

    port_raw = get("port")
    port: int | None = None
    if port_raw is not None:
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise MissingConfigError(f"PORT={port_raw!r} is not an integer") from exc

    return Settings(
        inference_url=get("inference_url"),
        inference_project=get("inference_project"),
        database_url=get("database_url"),
        storage_endpoint=get("storage_endpoint"),
        storage_access_key=get("storage_access_key"),
        storage_secret_key=get("storage_secret_key"),
        storage_bucket=get("storage_bucket"),
        storage_region=get("storage_region") or "us-east-1",
        loki_url=get("loki_url"),
        prometheus_pushgateway_url=get("prometheus_pushgateway_url"),
        analytics_dsn=get("analytics_dsn"),
        analytics_backend=analytics_backend,  # type: ignore[arg-type]  # validated above
        analytics_project=get("analytics_project"),
        auth_issuer=get("auth_issuer"),
        auth_jwks_url=get("auth_jwks_url"),
        auth_audience=get("auth_audience") or "mini-cloud",
        hf_token=get("hf_token"),
        port=port,
        log_level=log_level,  # type: ignore[arg-type]  # validated against _VALID_LOG_LEVELS
        app_env=app_env,  # type: ignore[arg-type]  # validated against _VALID_APP_ENVS
        app_name=get("app_name"),
    )


def _known_field_names() -> tuple[str, ...]:
    return tuple(f.name for f in fields(Settings))
