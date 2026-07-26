"""Correlation-ID propagation via a contextvar.

A correlation ID ties every log line and metric context of one request together and flows to
downstream services (already mandated by ``mlx-platform``). It lives in a
:class:`contextvars.ContextVar` — automatically isolated per-async-task and per-thread. Set it
once at the edge (the ASGI middleware does this) and every ``get_logger`` call underneath picks
it up with no threading.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

CORRELATION_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[str | None] = ContextVar("mini_cloud_correlation_id", default=None)


def new_correlation_id() -> str:
    """Generate a fresh correlation ID (a short uuid4 hex)."""
    return uuid.uuid4().hex


def get_correlation_id() -> str | None:
    """Return the current correlation ID, or ``None`` if none is bound."""
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    """Bind ``value`` as the current correlation ID (until reset or context exit)."""
    _correlation_id.set(value)


@contextmanager
def bind_correlation_id(value: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of the block, restoring the previous one on exit.

    ``value=None`` generates a fresh one. Use around a unit of work that has no HTTP edge (e.g. a
    job-queue handler) so its logs are correlated too.
    """
    cid = value or new_correlation_id()
    token = _correlation_id.set(cid)
    try:
        yield cid
    finally:
        _correlation_id.reset(token)
