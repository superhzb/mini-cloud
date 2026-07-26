"""Small, inspectable helpers for the SDK surfaces that do not belong to a domain handler.

The main flow naturally exercises most APIs. These helpers back the ``/debug/*`` tour endpoints
for configuration, migration state, and observability primitives while keeping secrets redacted.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

from mini_cloud.config import (
    CANONICAL_ENV_KEYS,
    MissingConfigError,
    Settings,
    load_dotenv,
)
from mini_cloud.db import (
    ConnSource,
    Job,
    JobQueue,
    Migration,
    RetryLater,
    applied_versions,
    connect,
    default_backoff,
    discover,
)
from mini_cloud.inference import InferenceClient, InferenceError
from mini_cloud.obs import (
    CORRELATION_HEADER,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_TOTAL,
    JsonFormatter,
    LokiHandler,
    get_correlation_id,
    new_correlation_id,
    observe_request,
    render_metrics,
    set_correlation_id,
)
from mini_cloud.storage import ObjectInfo, Storage, StorageError

from .resources import MIGRATIONS_DIR, Resources

_SECRET_FIELDS = {"storage_access_key", "storage_secret_key", "hf_token"}

# Class-qualified references make SDK method drift mechanically visible to the AST gate. Most are
# called naturally elsewhere in the app; this tuple covers the few administrative/error paths
# that cannot safely run in a no-services debug request (notably queue purge/nack/run_worker).
SDK_METHOD_CANARY = (
    Settings.require,
    JobQueue.create_schema,
    JobQueue.enqueue,
    JobQueue.dequeue,
    JobQueue.ack,
    JobQueue.nack,
    JobQueue.extend,
    JobQueue.depth,
    JobQueue.dead_letter_count,
    JobQueue.purge,
    JobQueue.requeue_dead_letter,
    JobQueue.work_once,
    JobQueue.run_worker,
    Job.attempts_remaining,
    Storage.from_settings,
    Storage.ensure_bucket,
    Storage.bucket_exists,
    Storage.put_bytes,
    Storage.put_stream,
    Storage.get_bytes,
    Storage.exists,
    Storage.delete,
    Storage.list,
    Storage.presigned_get_url,
    Storage.presigned_put_url,
    JsonFormatter.format,
    LokiHandler.emit,
    LokiHandler.close,
    InferenceClient.from_settings,
    InferenceClient.chat,
    InferenceClient.chat_messages,
    InferenceClient.embed,
    InferenceClient.models,
)


def config_snapshot(settings: Settings) -> dict[str, Any]:
    """Render canonical settings with secrets redacted and demonstrate fail-fast ``require``."""
    # A missing file is the documented no-op path, and avoids mutating process env in a request.
    dotenv_values = load_dotenv(Path(".ref-showcase-debug-does-not-exist"))
    values: dict[str, object] = {}
    for field in fields(settings):
        value = getattr(settings, field.name)
        values[field.name] = "<redacted>" if field.name in _SECRET_FIELDS and value else value

    try:
        settings.require("database_url")
        database_required = "configured"
    except MissingConfigError as exc:
        database_required = str(exc)

    return {
        "canonical_env": list(CANONICAL_ENV_KEYS),
        "settings": values,
        "database_require": database_required,
        "dotenv_missing_file": dotenv_values,
        # This explicit resolution is also the config canary for MINI_INFERENCE_PROJECT.
        "inference_project": settings.inference_project or settings.app_name,
    }


def migration_snapshot(res: Resources) -> dict[str, object]:
    """Show discovered/applied migrations and the SDK's direct-connection path."""
    source: ConnSource = res.pool if res.pool is not None else _direct_connection(res)
    migrations = discover(MIGRATIONS_DIR)
    try:
        applied = sorted(applied_versions(source))
        with connect(res.settings.require("database_url")) as direct:
            direct.execute("SELECT 1")
    finally:
        if res.pool is None:
            source.close()
    return {
        "discovered": [_migration_out(migration) for migration in migrations],
        "applied": applied,
        "queue_default_backoff_seconds": default_backoff(3),
        "retry_later_delay_seconds": RetryLater(delay_seconds=1.0).delay_seconds,
    }


def _migration_out(migration: Migration) -> dict[str, str]:
    return {"version": migration.version, "name": migration.name}


def _direct_connection(res: Resources) -> ConnSource:
    return connect(res.settings.require("database_url"))


def obs_snapshot() -> dict[str, object]:
    """Expose correlation and collector metadata without leaking metric samples or log secrets."""
    correlation_id = get_correlation_id() or new_correlation_id()
    # ``set`` is useful when adapting callbacks that cannot use the context manager. Setting the
    # already request-bound value here demonstrates it without extending correlation beyond scope.
    set_correlation_id(correlation_id)
    body, content_type = render_metrics()
    return {
        "correlation_header": CORRELATION_HEADER,
        "correlation_id": correlation_id,
        "metrics_content_type": content_type,
        "metrics_bytes": len(body),
        "sdk_collectors": [
            HTTP_REQUESTS_TOTAL._name,  # noqa: SLF001 - collector metadata for a debug tour
            HTTP_REQUEST_DURATION_SECONDS._name,  # noqa: SLF001
        ],
        "extension_point": observe_request.__name__,
        "logging_types": [JsonFormatter.__name__, LokiHandler.__name__],
        "error_types": [StorageError.__name__, InferenceError.__name__],
        "storage_listing_type": ObjectInfo.__name__,
    }
