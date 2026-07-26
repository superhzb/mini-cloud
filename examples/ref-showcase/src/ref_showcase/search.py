"""Semantic search — the read side of the ``inference`` *embed* tour.

Embeds the query with the **same path** the embed pipeline used to produce the corpus vectors —
the real gateway when ``MINI_INFERENCE_URL`` + an embed model are configured, the deterministic
offline fallback otherwise (see :func:`~ref_showcase.pipeline.fallback_embedding`) — then ranks the
corpus by cosine similarity **in-app** over the ``float8[]`` vectors read from Postgres.

Two deliberate choices worth calling out:

- **No pgvector.** The infra Postgres carries no vector extension, so ranking is a plain in-app
  cosine over a small corpus — a portability choice, not a scale one. It's honest about the size it
  targets (tens of documents), and it means the search tour needs nothing beyond stock Postgres.
- **Query and corpus share the embed path.** Cosine only compares vectors from the same embedder,
  so the query is embedded exactly the way the chunks were. Offline that's the fallback on both
  sides (fully reproducible, and searchable with no gateway); live it's the gateway on both sides.
  A stored vector whose dimensionality doesn't match the query's is skipped rather than silently
  mis-scored — the guard that keeps a mixed-provenance corpus from returning garbage.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from mini_cloud.obs import get_logger

from .metrics import SEARCH_LATENCY_SECONDS
from .pipeline import fallback_embedding
from .resources import Resources, embed_model

_log = get_logger("ref_showcase.search")


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One ranked result: which chunk (and its document) matched, and how strongly (0..1-ish)."""

    chunk_id: int
    document_id: int
    score: float


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; ``0.0`` if either has no magnitude.

    Raises :class:`ValueError` on a length mismatch — callers filter mismatched vectors out before
    ranking (a vector from a different embedder can't be compared), so reaching here with unequal
    lengths is a bug, not a data condition.
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def embed_query(res: Resources, text: str) -> list[float]:
    """Embed ``text`` the same way the corpus was embedded — gateway when configured, else the
    deterministic offline fallback. Mirrors :func:`~ref_showcase.pipeline.handle_embed` exactly so
    the query and the chunks it's compared against always come from one embedder."""
    model = embed_model()
    if res.inference is not None and model is not None:
        return res.require_inference().embed(text, model=model)[0]
    return fallback_embedding(text)


def semantic_search(res: Resources, query: str, *, limit: int = 5) -> list[SearchHit]:
    """Rank the embedded corpus against ``query`` by cosine similarity, best first.

    Reads every embedded chunk via :meth:`DocumentRepository.iter_embedded_chunks` (small-corpus
    scale by design), embeds the query through :func:`embed_query`, and returns the top ``limit``
    hits. Chunks whose vector dimensionality doesn't match the query's are skipped — the corpus is
    uniform per environment, but the guard keeps a stray cross-provenance vector from poisoning the
    ranking. Returns ``[]`` when the corpus has no embeddings yet.
    """
    backend = "gateway" if res.inference is not None and embed_model() is not None else "fallback"
    started = time.perf_counter()
    try:
        repo = res.require_repo()
        qvec = embed_query(res, query)
        hits: list[SearchHit] = []
        skipped = 0
        for chunk_id, document_id, vec in repo.iter_embedded_chunks():
            if len(vec) != len(qvec):
                skipped += 1
                continue
            hits.append(
                SearchHit(chunk_id=chunk_id, document_id=document_id, score=cosine(qvec, vec))
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        top = hits[:limit]
        _log.info(
            "semantic search",
            extra={
                "query_len": len(query),
                "backend": backend,
                "candidates": len(hits),
                "skipped": skipped,
                "returned": len(top),
            },
        )
        return top
    finally:
        SEARCH_LATENCY_SECONDS.labels(backend=backend).observe(time.perf_counter() - started)
