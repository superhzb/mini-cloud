"""ref-fastapi — the mini-cloud reference application.

A small but *complete* FastAPI service that exercises every SDK package end-to-end:

* ``config``    — all settings come from canonical env, never hardcoded.
* ``db``        — Postgres pool, SQL migrations, and the job queue.
* ``storage``   — a per-project MinIO bucket.
* ``obs``       — JSON logs, request metrics, correlation IDs, ``/metrics``.
* ``inference`` — a call to the canonical inference gateway.

It carries **no** bespoke SQLite writer, filesystem-as-store, or hand-rolled inference client —
that absence is the point (the platform acceptance criterion). It doubles as the seed for the
``fastapi`` template and must hold **7/7 on the scorecard** as SDK regression protection.
"""

__version__ = "0.1.0"
