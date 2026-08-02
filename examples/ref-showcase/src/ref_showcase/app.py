"""The FastAPI application: readiness probes and the document ingest/list/detail flow.

``obs.install`` wires JSON logging, request metrics, correlation IDs, and ``/metrics`` in one call
— observability on by default (scorecard #7). ``/healthz`` is liveness; ``/readyz`` reports whether
the backing services are actually reachable (what ``brbot-router`` and ``make check`` rely on).

This module holds the core flow that threads db + storage + queue together. The per-service *tour*
routers (config / storage / queue / inference / obs) land in later build steps; the pieces they
exercise already exist in ``domain.py`` / ``pipeline.py`` / ``resources.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from mini_cloud.auth import Principal
from mini_cloud.auth.fastapi import require_user
from mini_cloud.config import load_settings
from mini_cloud.inference import InferenceClient
from mini_cloud.obs import get_logger
from mini_cloud.obs.asgi import install
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from .analytics_tour import (
    EVENT_CHAT_STARTED,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_SEARCH_PERFORMED,
    recent_events,
    resolve_actor,
    showcase_funnel,
    showcase_retention,
    sql_reference,
    track,
)
from .auth_tour import SHOWCASE_APP, SHOWCASE_ROLE, auth_snapshot
from .domain import DocumentSummary
from .pipeline import SUMMARY_SYSTEM, submit_document
from .resources import WORK_QUEUES, Resources, build_resources
from .sdk_tour import config_snapshot, migration_snapshot, obs_snapshot
from .search import semantic_search
from .seed import SeedResult, seed_corpus

if TYPE_CHECKING:
    from mini_cloud.analytics import Analytics
    from mini_cloud.db import ConnSource

_log = get_logger("ref_showcase.app")
_WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build resources at startup (migrate + queue schema + bucket); close the pool on exit."""
    settings = load_settings()
    app.state.resources = build_resources(settings)
    _log.info("ref-showcase started", extra={"env": settings.app_env, "port": settings.port})
    yield
    res: Resources = app.state.resources
    if res.analytics is not None:
        res.analytics.close()  # flush the buffer + stop the background thread
    if res.analytics_pool is not None and hasattr(res.analytics_pool, "close"):
        res.analytics_pool.close()
    if res.pool is not None and hasattr(res.pool, "close"):
        res.pool.close()


def create_app() -> FastAPI:
    """Application factory (used by uvicorn and the tests)."""
    settings = load_settings()
    app = FastAPI(title=settings.app_name or "ref-showcase", version="0.1.0", lifespan=lifespan)
    install(app, settings)  # logging + metrics + /metrics + correlation IDs
    app.state.seed_lock = Lock()

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
        if r.inference is not None:
            checks["inference"] = True  # configured; a deep model probe is the inference tour's job
        ready = all(checks.values()) and bool(checks)
        if not ready:
            response.status_code = 503
        return {"ready": ready, "checks": checks}

    # --- document flow: ingest -> pipeline -> detail --------------------------------
    @app.post("/documents", status_code=202)
    def create_document(
        doc: DocumentIn,
        x_distinct_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> DocumentAccepted:
        """Store a document, create its rows atomically, and kick off the async pipeline.

        Exercises storage (raw blob), db (document+chunks+tags in one transaction), and the queue
        (fan-out ingest job) in one request. Returns the id to poll. This is funnel step 1
        (``document_uploaded``); the uploader's ``distinct_id`` threads into the pipeline so step 2
        (``document_processed``) is attributed to the same person.
        """
        distinct_id, session_id = resolve_actor(x_distinct_id, x_session_id)
        document_id, doc_key = submit_document(
            res(),
            title=doc.title,
            text=doc.text,
            source="api",
            tags=tuple(doc.tags),
            distinct_id=distinct_id,
        )
        track(
            res(),
            distinct_id,
            EVENT_DOCUMENT_UPLOADED,
            {"document_id": document_id, "tags": list(doc.tags)},
            session_id=session_id,
        )
        return DocumentAccepted(document_id=document_id, doc_key=doc_key)

    @app.get("/documents")
    def list_documents(
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        tag: str | None = None,
        status: str | None = None,
    ) -> DocumentPage:
        """Paginated, filterable list — the relational join/pagination tour over the corpus."""
        repo = res().require_repo()
        items = repo.list_documents(limit=limit, offset=offset, tag=tag, status=status)
        total = repo.count_documents(tag=tag, status=status)
        return DocumentPage(
            total=total,
            limit=limit,
            offset=offset,
            items=[_summary_out(s) for s in items],
        )

    @app.get("/documents/{document_id}")
    def get_document(document_id: int) -> DocumentDetailOut:
        """A single document with its ordered chunks and tags (404 if unknown)."""
        detail = res().require_repo().get_document(document_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        return DocumentDetailOut(
            document=_summary_out(detail.summary),
            chunks=[
                ChunkOut(ordinal=c.ordinal, content=c.content, embedded=c.has_embedding)
                for c in detail.chunks
            ],
        )

    @app.get("/queue/stats")
    def queue_stats() -> dict[str, object]:
        """Depth per queue + dead-letter total — proves the shared Postgres queue is in play."""
        q = res().require_queue()
        return {
            "depth": {name: q.depth(name) for name in WORK_QUEUES},
            "dead_letter": q.dead_letter_count(),
        }

    @app.post("/showcase/seed")
    def seed_showcase(count: int = Query(6, ge=1, le=12)) -> SeedResult:
        """Seed a bounded local demo and synchronously drain only its real pipeline queues.

        This unauthenticated mutation exists for the developer reference console, not as a
        production administration API. The non-blocking lock is process-local: the documented
        reference server uses one Uvicorn process, so cross-process coordination is out of scope.
        """
        lock: Lock = app.state.seed_lock
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="another showcase seed is already running")
        try:
            resources = res()
            try:
                resources.require_repo()
                resources.require_queue()
                resources.require_storage()
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return seed_corpus(resources, count=count, live=False)
        finally:
            lock.release()

    # --- config/db/obs inspection tours --------------------------------------------
    @app.get("/debug/config")
    def debug_config() -> dict[str, object]:
        """Typed canonical config, including fail-fast status, with secrets redacted."""
        return config_snapshot(res().settings)

    @app.get("/debug/db")
    def debug_db() -> dict[str, object]:
        """Discovered vs applied migrations plus direct-connection/backoff control surfaces."""
        if res().pool is None:
            raise HTTPException(status_code=503, detail="database unavailable — set DATABASE_URL")
        return migration_snapshot(res())

    @app.get("/debug/obs")
    def debug_obs() -> dict[str, object]:
        """Current correlation context and standard/custom collector metadata."""
        snapshot = obs_snapshot()
        snapshot["custom_collectors"] = [
            "documents_ingested_total",
            "search_latency_seconds",
            "queue_jobs_processed_total",
        ]
        return snapshot

    # --- storage tour: streams, listing, proxied + presigned access, delete ---------
    # The pipeline already writes docs/, chunks/, and summaries/ blobs; these endpoints expose the
    # rest of the Storage surface over that same bucket. Object keys are passed as a query param
    # (not a path segment) so a key with slashes needs no URL-path gymnastics.
    @app.post("/storage/uploads", status_code=201)
    async def upload_object(
        file: UploadFile = File(...),  # noqa: B008 — FastAPI dependency marker, evaluated per-request
        prefix: str = Form("uploads/"),
    ) -> UploadedOut:
        """Stream an uploaded file straight into object storage via ``put_stream`` (multipart under
        the hood — never buffers the whole body in the app), then hand back a presigned GET URL so
        the client can read it back directly from MinIO."""
        storage = res().require_storage()
        key = f"{prefix.rstrip('/')}/{file.filename or 'upload.bin'}"
        # UploadFile.file is a sync SpooledTemporaryFile — exactly the BinaryIO put_stream wants.
        storage.put_stream(key, file.file, content_type=file.content_type)
        _log.info("object uploaded", extra={"key": key, "content_type": file.content_type})
        return UploadedOut(key=key, get_url=storage.presigned_get_url(key))

    @app.get("/storage/objects")
    def list_objects(
        prefix: str = "",
        limit: int = Query(50, ge=1, le=1000),
    ) -> ObjectListOut:
        """List objects under ``prefix`` (e.g. ``docs/``, ``chunks/``, ``summaries/``), capped at
        ``limit`` — the paginated ``list(prefix=, limit=)`` tour over the namespaced bucket."""
        storage = res().require_storage()
        items = [
            ObjectOut(key=o.key, size=o.size, last_modified=str(o.last_modified))
            for o in storage.list(prefix, limit=limit)
        ]
        return ObjectListOut(prefix=prefix, count=len(items), items=items)

    @app.get("/storage/object/content")
    def download_object(key: str) -> Response:
        """App-proxied download: fetch the bytes with ``get_bytes`` and stream them back (contrast
        the presigned path, which bypasses the app entirely). 404 when the key is absent."""
        storage = res().require_storage()
        if not storage.exists(key):
            raise HTTPException(status_code=404, detail="object not found")
        return Response(content=storage.get_bytes(key), media_type="application/octet-stream")

    @app.post("/storage/presign")
    def presign(req: PresignIn) -> PresignOut:
        """Mint a time-limited URL for a direct client↔MinIO transfer, bypassing the app: a
        ``put`` URL (browser uploads straight to the bucket) or a ``get`` URL (direct download)."""
        storage = res().require_storage()
        if req.method == "put":
            url = storage.presigned_put_url(req.key, expires_in=req.expires_in)
        else:
            url = storage.presigned_get_url(req.key, expires_in=req.expires_in)
        return PresignOut(key=req.key, method=req.method, url=url, expires_in=req.expires_in)

    @app.delete("/storage/object", status_code=204)
    def delete_object(key: str) -> Response:
        """Delete an object (no-op if already gone — S3 semantics)."""
        res().require_storage().delete(key)
        return Response(status_code=204)

    # --- inference tour: semantic search, multi-turn chat, models, streaming --------
    def require_inference() -> InferenceClient:
        """The AI routes are live-required: a clear 503 (not a 500) when no gateway is configured,
        mirroring how ``/readyz`` reports ``inference: false``."""
        r = res()
        if r.inference is None:
            raise HTTPException(
                status_code=503, detail="inference unavailable — set MINI_INFERENCE_URL"
            )
        return r.inference

    @app.post("/search")
    def search(
        req: SearchIn,
        x_distinct_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> SearchResultsOut:
        """Embed the query and rank the corpus by in-app cosine over the stored ``float8[]`` vecs.

        Degrades like the pipeline: with a gateway it embeds via ``inference.embed``; offline it
        uses the same deterministic fallback the corpus was embedded with, so search is meaningful
        (and testable) even with no gateway. Each hit is joined back to its document title. Funnel
        step 3 (``search_performed``)."""
        r = res()
        hits = semantic_search(r, req.query, limit=req.limit)
        repo = r.require_repo()
        titles: dict[int, str] = {}
        out: list[SearchHitOut] = []
        for h in hits:
            if h.document_id not in titles:
                detail = repo.get_document(h.document_id)
                titles[h.document_id] = detail.summary.title if detail else "(deleted)"
            out.append(
                SearchHitOut(
                    document_id=h.document_id,
                    chunk_id=h.chunk_id,
                    title=titles[h.document_id],
                    score=round(h.score, 6),
                )
            )
        distinct_id, session_id = resolve_actor(x_distinct_id, x_session_id)
        track(
            r,
            distinct_id,
            EVENT_SEARCH_PERFORMED,
            {"query": req.query, "hits": len(out)},
            session_id=session_id,
        )
        return SearchResultsOut(query=req.query, count=len(out), hits=out)

    @app.post("/documents/{document_id}/chat")
    def chat_over_document(
        document_id: int,
        req: ChatIn,
        x_distinct_id: str | None = Header(default=None),
        x_session_id: str | None = Header(default=None),
    ) -> ChatOut:
        """Multi-turn chat grounded in one document: the document's chunks become a system context,
        the caller's turns follow, and ``chat_messages`` returns the reply. Live-required (503 with
        no gateway); 404 for an unknown document. Funnel step 4 (``chat_started``)."""
        ai = require_inference()
        detail = res().require_repo().get_document(document_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        distinct_id, session_id = resolve_actor(x_distinct_id, x_session_id)
        track(
            res(),
            distinct_id,
            EVENT_CHAT_STARTED,
            {"document_id": document_id, "turns": len(req.messages)},
            session_id=session_id,
        )
        context = "\n".join(c.content for c in detail.chunks)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    f"You answer questions about the document titled {detail.summary.title!r}. "
                    f"Use only this content:\n{context}"
                ),
            },
            *({"role": m.role, "content": m.content} for m in req.messages),
        ]
        reply = ai.chat_messages(messages, max_tokens=req.max_tokens)
        return ChatOut(document_id=document_id, reply=reply, turns=len(req.messages))

    @app.get("/inference/models")
    def list_models() -> dict[str, object]:
        """List the model IDs the gateway advertises (``inference.models()``)."""
        return {"models": require_inference().models()}

    @app.get("/documents/{document_id}/summary/stream")
    def stream_summary(document_id: int) -> StreamingResponse:
        """Stream a fresh one-sentence summary token-by-token as Server-Sent Events, driven through
        the ``.openai`` passthrough (``stream=True``) — the escape hatch for anything the handy
        methods don't cover. Live-required; 404 for an unknown document."""
        ai = require_inference()
        detail = res().require_repo().get_document(document_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        if not ai.default_model:
            raise HTTPException(status_code=503, detail="no default inference model configured")
        text = "\n".join(c.content for c in detail.chunks)

        def event_stream() -> Iterator[str]:
            stream = ai.openai.chat.completions.create(
                model=ai.default_model or "",
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM},
                    {"role": "user", "content": text},
                ],
                max_tokens=64,
                stream=True,
            )
            for chunk in stream:
                choices = chunk.choices
                if choices and choices[0].delta.content:
                    yield f"data: {choices[0].delta.content}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    # --- analytics tour: capture, identify/alias, funnel, retention, recent stream --
    def require_analytics() -> Analytics:
        """Product analytics is opt-in: a clear 503 (not a 500) when no analytics DB is configured,
        mirroring how ``/readyz`` reports missing dependencies."""
        r = res()
        if r.analytics is None:
            raise HTTPException(
                status_code=503, detail="analytics unavailable — set MINI_ANALYTICS_DSN"
            )
        return r.analytics

    def require_analytics_pool() -> ConnSource:
        r = res()
        if r.analytics_pool is None:
            raise HTTPException(
                status_code=503, detail="analytics unavailable — set MINI_ANALYTICS_DSN"
            )
        return r.analytics_pool

    @app.post("/analytics/capture", status_code=202)
    def analytics_capture(req: CaptureIn) -> dict[str, str]:
        """Buffer one arbitrary product event (the manual tour of ``capture``). Never blocks."""
        require_analytics().capture(
            req.distinct_id, req.event, req.properties, session_id=req.session_id
        )
        return {"status": "buffered", "event": req.event}

    @app.post("/analytics/identify")
    def analytics_identify(req: IdentifyIn) -> dict[str, str]:
        """Upsert a person and their properties — anchor of the anonymous→identified stitch."""
        require_analytics().identify(req.distinct_id, req.properties)
        return {"status": "ok", "distinct_id": req.distinct_id}

    @app.post("/analytics/alias")
    def analytics_alias(req: AliasIn) -> dict[str, str]:
        """Stitch an anonymous id to an identified one (the login-ish moment). Query-time funnel/
        retention then collapse the two into one person."""
        require_analytics().alias(req.previous_id, req.distinct_id)
        return {"status": "ok", "previous_id": req.previous_id, "distinct_id": req.distinct_id}

    @app.get("/analytics/funnel")
    def analytics_funnel() -> FunnelOut:
        """Run the instrumented 4-step funnel and return per-step counts + conversion. Flushes the
        buffer first so a just-captured event is visible (identity resolved at query time)."""
        analytics = require_analytics()
        analytics.flush()
        result = showcase_funnel(require_analytics_pool(), analytics.project)
        return FunnelOut(
            project=analytics.project,
            entered=result.entered,
            converted=result.converted,
            overall_conversion=round(result.overall_conversion, 4),
            steps=[
                FunnelStepOut(
                    event=s.event,
                    count=s.count,
                    conversion_from_prev=round(s.conversion_from_prev, 4),
                    conversion_from_top=round(s.conversion_from_top, 4),
                )
                for s in result.steps
            ],
        )

    @app.get("/analytics/retention")
    def analytics_retention() -> dict[str, object]:
        """Weekly retention cohorts anchored on the first funnel step (``document_uploaded``)."""
        analytics = require_analytics()
        analytics.flush()
        result = showcase_retention(require_analytics_pool(), analytics.project)
        return {
            "project": analytics.project,
            "anchor_event": result.anchor_event,
            "cells": [
                {"cohort_week": week, "period": period, "active": active}
                for week, period, active in result.cells
            ],
        }

    @app.get("/analytics/events")
    def analytics_events(limit: int = Query(20, ge=1, le=200)) -> dict[str, object]:
        """The most recent raw events for this project, newest first — the append-only stream."""
        analytics = require_analytics()
        analytics.flush()
        events = recent_events(require_analytics_pool(), analytics.project, limit=limit)
        return {
            "project": analytics.project,
            "count": len(events),
            "events": [
                {
                    "event": e.event,
                    "distinct_id": e.distinct_id,
                    "session_id": e.session_id,
                    "properties": e.properties,
                    "timestamp": str(e.timestamp),
                    "correlation_id": e.correlation_id,
                }
                for e in events
            ],
        }

    @app.get("/analytics/sql")
    def analytics_sql() -> dict[str, str]:
        """The generated funnel/retention SQL + the package's shipped migrations dir — an
        inspectable view of the query-time-identity machinery."""
        return sql_reference()

    # --- identity tour: a protected endpoint + an inspectable auth-config view ------
    # `require_user` is the whole plug-and-play line: it verifies the caller's platform JWT and
    # enforces `grants["ref-showcase"] >= "member"`. The verifier is the process default, built
    # lazily from MINI_AUTH_ISSUER (tests inject an offline one via mini_cloud.auth.configure).
    require_showcase_member = require_user(app=SHOWCASE_APP, role=SHOWCASE_ROLE)

    @app.get("/auth/config")
    def auth_config() -> dict[str, object]:
        """Is identity wired, and to which issuer/JWKS? Unprotected — nothing here is secret."""
        return auth_snapshot(res().settings)

    @app.get("/auth/whoami")
    def whoami(user: Principal = Depends(require_showcase_member)) -> WhoAmIOut:  # noqa: B008
        """The protected endpoint: 200 only for a valid platform JWT whose holder is at least a
        ``member`` of ``ref-showcase``. 401 without a good token, 403 without the grant."""
        return WhoAmIOut(
            sub=user.sub,
            email=user.email,
            role=user.role_for(SHOWCASE_APP),
            grants=user.grants,
        )

    @app.get("/")
    def root() -> dict[str, str]:
        return {"app": app.title, "docs": "/docs", "metrics": "/metrics", "ui": "/ui/"}

    @app.get("/ui", include_in_schema=False)
    def ui_redirect() -> RedirectResponse:
        return RedirectResponse("/ui/", status_code=307)

    app.mount("/ui", StaticFiles(directory=_WEB_DIR, html=True), name="console")

    return app


# --- request/response models --------------------------------------------------------
class DocumentIn(BaseModel):
    title: str
    text: str
    tags: list[str] = []


class DocumentAccepted(BaseModel):
    document_id: int
    doc_key: str


class SummaryOut(BaseModel):
    id: int
    title: str
    source: str
    status: str
    chunk_count: int
    tags: list[str]
    summary_key: str | None


class DocumentPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SummaryOut]


class ChunkOut(BaseModel):
    ordinal: int
    content: str
    embedded: bool


class DocumentDetailOut(BaseModel):
    document: SummaryOut
    chunks: list[ChunkOut]


# --- storage tour models ------------------------------------------------------------
class UploadedOut(BaseModel):
    key: str
    get_url: str


class ObjectOut(BaseModel):
    key: str
    size: int
    last_modified: str


class ObjectListOut(BaseModel):
    prefix: str
    count: int
    items: list[ObjectOut]


class PresignIn(BaseModel):
    key: str
    method: Literal["get", "put"] = "get"
    expires_in: int = 3600


class PresignOut(BaseModel):
    key: str
    method: str
    url: str
    expires_in: int


# --- inference tour models ----------------------------------------------------------
class SearchIn(BaseModel):
    query: str
    limit: int = 5


class SearchHitOut(BaseModel):
    document_id: int
    chunk_id: int
    title: str
    score: float


class SearchResultsOut(BaseModel):
    query: str
    count: int
    hits: list[SearchHitOut]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"] = "user"
    content: str


class ChatIn(BaseModel):
    messages: list[ChatTurn]
    max_tokens: int = 256


class ChatOut(BaseModel):
    document_id: int
    reply: str
    turns: int


# --- analytics tour models ----------------------------------------------------------
class CaptureIn(BaseModel):
    distinct_id: str
    event: str
    properties: dict[str, Any] = {}
    session_id: str | None = None


class IdentifyIn(BaseModel):
    distinct_id: str
    properties: dict[str, Any] = {}


class AliasIn(BaseModel):
    previous_id: str
    distinct_id: str


class FunnelStepOut(BaseModel):
    event: str
    count: int
    conversion_from_prev: float
    conversion_from_top: float


class FunnelOut(BaseModel):
    project: str
    entered: int
    converted: int
    overall_conversion: float
    steps: list[FunnelStepOut]


# --- identity tour models -----------------------------------------------------------
class WhoAmIOut(BaseModel):
    sub: str
    email: str | None
    role: str | None
    grants: dict[str, str]


def _summary_out(s: DocumentSummary) -> SummaryOut:
    return SummaryOut(
        id=s.id,
        title=s.title,
        source=s.source,
        status=s.status,
        chunk_count=s.chunk_count,
        tags=s.tags,
        summary_key=s.summary_key,
    )


# Module-level ASGI app for `uvicorn ref_showcase.app:app`.
app = create_app()
