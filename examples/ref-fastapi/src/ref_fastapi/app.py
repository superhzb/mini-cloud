"""The FastAPI application: readiness probes, and a small notes→summary demo flow.

``obs.install`` wires JSON logging, request metrics, correlation IDs, and ``/metrics`` in one call
— observability is on by default (scorecard #7). ``/healthz`` is liveness (process up);
``/readyz`` reports whether the backing services are actually reachable, which is what
``brbot-router`` readiness probes and ``make check`` rely on.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from mini_cloud.config import load_settings
from mini_cloud.obs import get_logger
from mini_cloud.obs.asgi import install
from pydantic import BaseModel

from .resources import SUMMARIZE_QUEUE, Resources, build_resources
from .tasks import summary_key

_log = get_logger("ref_fastapi.app")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build resources at startup (migrations + queue schema + bucket), tear the pool down on
    shutdown."""
    settings = load_settings()
    app.state.resources = build_resources(settings)
    _log.info("ref-fastapi started", extra={"env": settings.app_env, "port": settings.port})
    yield
    res: Resources = app.state.resources
    if res.pool is not None and hasattr(res.pool, "close"):
        res.pool.close()


def create_app() -> FastAPI:
    """Application factory (used by uvicorn and the tests)."""
    settings = load_settings()
    app = FastAPI(title=settings.app_name or "ref-fastapi", version="0.1.0", lifespan=lifespan)
    install(app, settings)  # logging + metrics + /metrics + correlation IDs

    def res() -> Resources:
        return app.state.resources

    # --- probes ---------------------------------------------------------------------
    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness: the process is up and serving. Never touches a backing service."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, object]:
        """Readiness: are the dependencies reachable? 200 when ready, 503 otherwise."""
        checks: dict[str, bool] = {}
        r = res()
        if r.pool is not None:
            try:
                with r.pool.connection() as conn:  # type: ignore[union-attr]  # pool in this app
                    conn.execute("SELECT 1")
                checks["db"] = True
            except Exception:  # noqa: BLE001 — a failed probe is a False check, not a 500
                checks["db"] = False
        if r.storage is not None:
            checks["storage"] = r.storage.bucket_exists()
        ready = all(checks.values()) and bool(checks)
        if not ready:
            response.status_code = 503
        return {"ready": ready, "checks": checks}

    # --- demo flow: notes -> summary ------------------------------------------------
    @app.post("/notes", status_code=202)
    def create_note(note: NoteIn) -> NoteAccepted:
        """Store a note's text in the bucket and enqueue a summarize job. Returns the keys to
        poll. Demonstrates storage + the job queue in one request."""
        r = res()
        storage = r.require_storage()
        queue = r.require_queue()
        note_key = f"notes/{uuid.uuid4().hex}.txt"
        storage.put_bytes(note_key, note.text.encode("utf-8"), content_type="text/plain")
        # dedupe_key makes re-submission of the same note idempotent at the queue layer.
        job_id = queue.enqueue(SUMMARIZE_QUEUE, {"note_key": note_key}, dedupe_key=note_key)
        _log.info("note accepted", extra={"note_key": note_key, "job_id": job_id})
        return NoteAccepted(note_key=note_key, summary_key=summary_key(note_key), job_id=job_id)

    @app.get("/notes/{note_id}/summary")
    def get_summary(note_id: str) -> SummaryOut:
        """Return the summary once the worker has produced it (404 until then)."""
        r = res()
        storage = r.require_storage()
        key = summary_key(f"notes/{note_id}.txt")
        try:
            text = storage.get_bytes(key).decode("utf-8")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="summary not ready") from exc
        return SummaryOut(summary=text)

    @app.get("/queue/depth")
    def queue_depth() -> dict[str, int]:
        """Live queue depth — proves the shared Postgres queue is in play."""
        return res().require_queue().depth(SUMMARIZE_QUEUE)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"app": app.title, "docs": "/docs", "metrics": "/metrics"}

    return app


class NoteIn(BaseModel):
    text: str


class NoteAccepted(BaseModel):
    note_key: str
    summary_key: str
    job_id: int | None


class SummaryOut(BaseModel):
    summary: str


# Module-level ASGI app for `uvicorn ref_fastapi.app:app`.
app = create_app()
