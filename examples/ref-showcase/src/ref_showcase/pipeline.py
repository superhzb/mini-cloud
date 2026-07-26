"""The queue-driven pipeline — the ``db`` *job-queue* tour, plus the storage/inference threads.

Flow (fan-out across three queues)::

    submit_document → enqueue ``ingest``
      ingest   : persist each chunk blob, mark 'chunked', enqueue ``embed`` + ``summarize``
      embed    : embed each chunk (gateway or offline fallback), store the float8[] vector
      summarize: summarise the doc (gateway or offline fallback), store it, mark 'ready'

Two demo handlers exercise the remaining queue features the fan-out doesn't:

    long   : a slow job that calls :meth:`JobQueue.extend` to heartbeat past its visibility timeout
    poison : a job that always raises, so it backs off and finally dead-letters (then an operator
             replays it with :meth:`JobQueue.requeue_dead_letter`)

**Correlation across the enqueue boundary.** ``bind_correlation_id`` is contextvar-based and
in-process only — it does not survive ``enqueue``. So every job payload *carries* the correlation
id, and :func:`dispatch` re-binds it on dequeue in the worker: the payload is the carrier, the
contextvar is the per-side binder. Downstream jobs enqueued by a handler propagate the same id.

At-least-once delivery means every handler is **idempotent** — re-running overwrites the same
chunk/summary keys and skips already-embedded chunks.
"""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING
from uuid import uuid4

from mini_cloud.db import Job, RetryLater
from mini_cloud.obs import bind_correlation_id, get_correlation_id, get_logger, new_correlation_id

from .analytics_tour import EVENT_DOCUMENT_PROCESSED, track
from .metrics import DOCUMENTS_INGESTED_TOTAL, QUEUE_JOBS_PROCESSED_TOTAL
from .resources import (
    EMBED_QUEUE,
    INGEST_QUEUE,
    LONG_QUEUE,
    POISON_QUEUE,
    SUMMARIZE_QUEUE,
    Resources,
    embed_model,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_log = get_logger("ref_showcase.pipeline")

SUMMARY_SYSTEM = "You summarise a document in one short sentence. Output only the summary."
EMBED_DIMS = 32  # offline-fallback vector width (a real gateway returns its own dimensionality)


# --- pure helpers (unit-tested, no services) ----------------------------------------
def chunk_text(text: str, *, max_chars: int = 280) -> list[str]:
    """Split text into chunks on paragraph/sentence-ish boundaries, capped at ``max_chars``.

    Deterministic and dependency-free — the ingest stage and the seed corpus both rely on it, and
    it's the unit-testable seam of the pipeline. Always returns at least one chunk for non-empty
    input; empty/whitespace input returns ``[]``.
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        # Greedy pack whole words up to the cap.
        current = ""
        for word in para.split():
            if current and len(current) + 1 + len(word) > max_chars:
                chunks.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            chunks.append(current)
    return chunks


def fallback_embedding(text: str, *, dims: int = EMBED_DIMS) -> list[float]:
    """A deterministic, offline pseudo-embedding: a normalised hashed bag-of-words vector.

    Keeps the pipeline (and its live tests) runnable without a real gateway, and — being
    deterministic — makes cosine search reproducible. The real ``inference.embed`` replaces it when
    ``MINI_INFERENCE_URL`` + an embed model are configured.
    """
    vec = [0.0] * dims
    for token in text.lower().split():
        digest = hashlib.sha1(token.encode("utf-8")).hexdigest()  # noqa: S324 — not security, a hash bucket
        vec[int(digest, 16) % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def fallback_summary(text: str) -> str:
    """Trivial offline summary: the first non-empty line, clipped. Mirrors ref-fastapi's fallback
    so the demo runs without MLX."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), text.strip())
    return first[:140]


# --- entrypoint (used by app, seed, tests) ------------------------------------------
def submit_document(
    res: Resources,
    *,
    title: str,
    text: str,
    source: str = "api",
    tags: tuple[str, ...] = (),
    correlation_id: str | None = None,
    doc_key: str | None = None,
    distinct_id: str | None = None,
) -> tuple[int, str]:
    """Store the raw body, create the document + chunks + tags atomically, and enqueue ingest.

    Returns ``(document_id, doc_key)``. The correlation id is stamped into the ingest payload so
    the whole async pipeline stays correlated back to this call. The uploader's ``distinct_id`` (if
    any) rides along too, so the pipeline can attribute the ``document_processed`` funnel event to
    the same person who uploaded — the seed passes ``None`` (it emits its own event stream).
    """
    storage = res.require_storage()
    repo = res.require_repo()
    queue = res.require_queue()

    doc_key = doc_key or f"docs/{uuid4().hex}.txt"
    storage.put_bytes(doc_key, text.encode("utf-8"), content_type="text/plain")

    pieces = chunk_text(text)
    document_id = repo.create_document(
        doc_key=doc_key, title=title, source=source, chunks=pieces, tags=tags
    )

    cid = correlation_id or get_correlation_id() or new_correlation_id()
    queue.enqueue(
        INGEST_QUEUE,
        {"document_id": document_id, "correlation_id": cid, "distinct_id": distinct_id},
        dedupe_key=f"ingest:{document_id}",  # idempotent re-ingest
    )
    _log.info("document submitted", extra={"document_id": document_id, "chunks": len(pieces)})
    return document_id, doc_key


# --- handlers -----------------------------------------------------------------------
def handle_ingest(res: Resources, job: Job) -> None:
    """Persist each chunk blob, mark the document 'chunked', then fan out to embed + summarize."""
    document_id = int(job.payload["document_id"])
    cid = job.payload.get("correlation_id")
    distinct_id = job.payload.get("distinct_id")
    repo = res.require_repo()
    storage = res.require_storage()
    queue = res.require_queue()

    detail = repo.get_document(document_id)
    first_ingest = detail is not None and detail.summary.status == "new"
    for chunk in repo.chunks_for(document_id):
        if chunk.chunk_key is None:  # idempotent: skip chunks already persisted
            key = f"chunks/{document_id}/{chunk.ordinal}.txt"
            storage.put_bytes(key, chunk.content.encode("utf-8"), content_type="text/plain")
            repo.set_chunk_blob(chunk.id, key)
    repo.set_status(document_id, "chunked")
    if first_ingest and detail is not None:
        DOCUMENTS_INGESTED_TOTAL.labels(source=detail.summary.source).inc()

    payload = {"document_id": document_id, "correlation_id": cid, "distinct_id": distinct_id}
    queue.enqueue(EMBED_QUEUE, payload, dedupe_key=f"embed:{document_id}")
    queue.enqueue(SUMMARIZE_QUEUE, payload, dedupe_key=f"summarize:{document_id}")
    _log.info("ingested", extra={"document_id": document_id})


def handle_embed(res: Resources, job: Job) -> None:
    """Embed each not-yet-embedded chunk and store the vector (gateway or offline fallback)."""
    document_id = int(job.payload["document_id"])
    repo = res.require_repo()
    model = embed_model()
    use_gateway = res.inference is not None and model is not None

    for chunk in repo.chunks_missing_embedding(document_id):
        if use_gateway:
            vector = res.require_inference().embed(chunk.content, model=model)[0]
        else:
            vector = fallback_embedding(chunk.content)
        repo.set_chunk_embedding(chunk.id, vector)
    _log.info("embedded", extra={"document_id": document_id, "gateway": use_gateway})


def handle_summarize(res: Resources, job: Job) -> None:
    """Summarise the document, store the summary blob, and mark it 'ready'."""
    document_id = int(job.payload["document_id"])
    repo = res.require_repo()
    storage = res.require_storage()

    chunks = repo.chunks_for(document_id)
    text = "\n".join(c.content for c in chunks)

    if res.inference is not None and res.inference.default_model:
        summary = res.require_inference().chat(text, system=SUMMARY_SYSTEM, max_tokens=64)
    else:
        summary = fallback_summary(text)

    summary_key = f"summaries/{document_id}.txt"
    storage.put_bytes(summary_key, summary.encode("utf-8"), content_type="text/plain")
    repo.set_summary(document_id, summary_key)

    # Funnel step 2: the document is fully processed. Attributed to the uploader (carried in the
    # payload) so it lands on the same person as step 1. Seed-originated docs carry no distinct_id
    # and are skipped here — the seed emits its own deterministic event stream instead.
    distinct_id = job.payload.get("distinct_id")
    if distinct_id:
        track(res, distinct_id, EVENT_DOCUMENT_PROCESSED, {"document_id": document_id})
    _log.info("summarised", extra={"document_id": document_id, "chars_out": len(summary)})


def handle_long(res: Resources, job: Job) -> None:
    """A long job that heartbeats: it :meth:`JobQueue.extend`\\ s its own visibility deadline so a
    second worker never starts it, then finishes. The demo passes ``steps`` in the payload."""
    queue = res.require_queue()
    steps = int(job.payload.get("steps", 3))
    for i in range(steps):
        queue.extend(job, seconds=30)  # push the visibility deadline out (heartbeat)
        _log.info("long job heartbeat", extra={"job_id": job.id, "step": i + 1, "of": steps})
    _log.info("long job done", extra={"job_id": job.id})


def handle_poison(_res: Resources, job: Job) -> None:
    """Always fails — the worker nacks it (backoff), and it dead-letters once attempts exhaust.
    Exists so the queue tour can demonstrate the dead-letter path and ``requeue_dead_letter``."""
    raise RuntimeError(f"poison job {job.id} always fails (attempt {job.attempts})")


# --- dispatch (worker glue) ---------------------------------------------------------
HANDLERS: dict[str, Callable[[Resources, Job], None]] = {
    INGEST_QUEUE: handle_ingest,
    EMBED_QUEUE: handle_embed,
    SUMMARIZE_QUEUE: handle_summarize,
    LONG_QUEUE: handle_long,
    POISON_QUEUE: handle_poison,
}


def dispatch(res: Resources, job: Job) -> None:
    """Run the handler for ``job.queue`` under the job's carried correlation id.

    Re-binding here (not at enqueue) is the whole point: correlation crosses the queue boundary via
    the payload, and this rebinds it so the handler's logs correlate to the originating request.
    """
    with bind_correlation_id(job.payload.get("correlation_id")):
        handler = HANDLERS.get(job.queue)
        if handler is None:
            QUEUE_JOBS_PROCESSED_TOTAL.labels(queue=job.queue, outcome="unhandled").inc()
            raise RuntimeError(f"no handler registered for queue {job.queue!r}")
        try:
            handler(res, job)
        except RetryLater:
            QUEUE_JOBS_PROCESSED_TOTAL.labels(queue=job.queue, outcome="retry").inc()
            raise
        except Exception:
            QUEUE_JOBS_PROCESSED_TOTAL.labels(queue=job.queue, outcome="failed").inc()
            raise
        else:
            QUEUE_JOBS_PROCESSED_TOTAL.labels(queue=job.queue, outcome="succeeded").inc()
