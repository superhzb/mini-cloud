"""Event sinks — where captured events, persons, and aliases land.

The :class:`EventSink` protocol is the seam that makes ``MINI_ANALYTICS_BACKEND`` a one-env flip:
:class:`PostgresSink` (default) writes to the shared analytics Postgres; :class:`PostHogSink` is
the documented graduation seam to real PostHog (a thin ``posthog-python`` wrapper behind the
``[posthog]`` extra, stubbed in v0).

The write path stays a *dumb append*: :meth:`PostgresSink.write_events` inserts raw ``distinct_id``
with ``person_id`` NULL — never a per-event person lookup. Identity is stitched at query time (see
``funnels``), while :meth:`~PostgresSink.identify` / :meth:`~PostgresSink.alias` maintain the person
and alias tables out of band.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from mini_cloud.db import ConnSource, acquire
from psycopg.types.json import Jsonb

from .events import Event

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from mini_cloud.config import Settings


class AnalyticsError(RuntimeError):
    """Raised for analytics misconfiguration (e.g. no DSN, or a backend whose deps are missing)."""


@runtime_checkable
class EventSink(Protocol):
    """The backend contract. A sink accepts batched events and maintains person/alias state.

    Implementations must be safe to call from the client's background flush thread. ``close()`` is
    for sink-owned resources; a sink handed a caller-owned pool must leave that pool open.
    """

    def write_events(self, events: Sequence[Event]) -> None:
        """Persist a batch of events (append-only). Called from the flush thread."""
        ...

    def identify(self, distinct_id: str, properties: Mapping[str, Any]) -> None:
        """Upsert the person keyed by ``distinct_id``, merging ``properties``."""
        ...

    def alias(self, previous_id: str, distinct_id: str) -> None:
        """Map an anonymous ``previous_id`` to an identified ``distinct_id``."""
        ...

    def close(self) -> None:
        """Release any sink-owned resources. A no-op for a borrowed connection source."""
        ...


# --- SQL (query-time identity resolution lives in funnels; these are the write-side statements) ---
_INSERT_EVENT_SQL = """
    INSERT INTO analytics_events
        (event, distinct_id, project, session_id, properties, timestamp, correlation_id)
    VALUES (%s, %s, %s, %s, %s, coalesce(%s, now()), %s)
"""

# The identified distinct_id IS the person_id. Re-identifying merges properties and unions the
# accumulated distinct_ids without duplicates.
_UPSERT_PERSON_SQL = """
    INSERT INTO analytics_persons (person_id, distinct_ids, properties, first_seen, last_seen)
    VALUES (%s, ARRAY[%s]::text[], %s, now(), now())
    ON CONFLICT (person_id) DO UPDATE SET
        properties   = analytics_persons.properties || EXCLUDED.properties,
        distinct_ids = (
            SELECT array_agg(DISTINCT d)
            FROM unnest(analytics_persons.distinct_ids || EXCLUDED.distinct_ids) AS d
        ),
        last_seen    = now()
"""

_UPSERT_ALIAS_SQL = """
    INSERT INTO analytics_person_aliases (previous_id, distinct_id, created_at)
    VALUES (%s, %s, now())
    ON CONFLICT (previous_id)
        DO UPDATE SET distinct_id = EXCLUDED.distinct_id, created_at = now()
"""


class PostgresSink:
    """The default sink: writes to the shared analytics Postgres via ``mini_cloud.db``.

    Bound to a :data:`~mini_cloud.db.ConnSource` (a pool in an app, a connection in tests) that the
    **caller owns** — the sink borrows a connection per call and never closes the source.
    """

    def __init__(self, source: ConnSource) -> None:
        self._source = source

    def write_events(self, events: Sequence[Event]) -> None:
        """Batch-insert events with one ``executemany`` round-trip. ``person_id`` stays NULL."""
        if not events:
            return
        rows = [
            (
                e.event,
                e.distinct_id,
                e.project,
                e.session_id,
                Jsonb(e.properties),
                e.timestamp,
                e.correlation_id,
            )
            for e in events
        ]
        with acquire(self._source) as conn:
            with conn.cursor() as cur:
                cur.executemany(_INSERT_EVENT_SQL, rows)
            if not conn.autocommit:
                conn.commit()

    def identify(self, distinct_id: str, properties: Mapping[str, Any]) -> None:
        with acquire(self._source) as conn:
            conn.execute(_UPSERT_PERSON_SQL, (distinct_id, distinct_id, Jsonb(dict(properties))))
            if not conn.autocommit:
                conn.commit()

    def alias(self, previous_id: str, distinct_id: str) -> None:
        with acquire(self._source) as conn:
            conn.execute(_UPSERT_ALIAS_SQL, (previous_id, distinct_id))
            if not conn.autocommit:
                conn.commit()

    def close(self) -> None:
        """No-op: the connection source is owned by the caller (an app's shared pool)."""


class PostHogSink:
    """Graduation seam to real PostHog — the answer to *"can we just ship to PostHog later?"*.

    A thin wrapper over ``posthog-python`` (the ``[posthog]`` extra), selected by
    ``MINI_ANALYTICS_BACKEND=posthog``. **Stubbed in v0**: the write methods raise
    :class:`NotImplementedError` so the seam is visible and typed without pulling the dependency
    into the default install. Wiring it up is the documented Phase-C follow-up.
    """

    def __init__(self, client: Any = None) -> None:  # noqa: ANN401 — posthog.Posthog, kept loose
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> PostHogSink:
        """Build from settings once the seam is implemented. Stubbed: fails fast with guidance."""
        raise AnalyticsError(
            "MINI_ANALYTICS_BACKEND=posthog is a documented v0 stub — install the [posthog] extra "
            "and implement PostHogSink to ship to real PostHog (see docs/analytics-plan.md)."
        )

    def write_events(self, events: Sequence[Event]) -> None:
        raise NotImplementedError("PostHogSink is a v0 stub (graduation seam)")

    def identify(self, distinct_id: str, properties: Mapping[str, Any]) -> None:
        raise NotImplementedError("PostHogSink is a v0 stub (graduation seam)")

    def alias(self, previous_id: str, distinct_id: str) -> None:
        raise NotImplementedError("PostHogSink is a v0 stub (graduation seam)")

    def close(self) -> None:
        """Flush/close the underlying posthog client when implemented; a no-op for the stub."""
        if self._client is not None:
            self._client.shutdown()
