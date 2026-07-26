"""The :class:`Event` value type — one captured product-analytics event.

Mirrors the ``analytics_events`` columns a *writer* is responsible for. Deliberately absent:
``person_id`` (resolved at query time, never on the write path) and ``received_at`` (stamped by the
store on ingest). A ``None`` :attr:`Event.timestamp` means "let the store default it to ``now()``".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    """One captured event, PostHog-shaped: a person (``distinct_id``) did ``event`` with
    ``properties``, tagged with the ``project`` it belongs to."""

    event: str
    distinct_id: str
    project: str
    properties: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime | None = None
    session_id: str | None = None
    correlation_id: str | None = None
