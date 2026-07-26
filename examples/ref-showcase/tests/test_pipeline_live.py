"""Full end-to-end pipeline test — needs Postgres AND MinIO (skips when storage is absent).

Proves the fan-out: submit → ingest → (embed + summarize) drains to a 'ready' document with all
chunks embedded and a summary blob in storage. Runs offline (no gateway) via the deterministic
fallbacks, so only Postgres + MinIO are required. Under `check-live` (ephemeral PG, no MinIO) it
skips; run it against the full stack with STORAGE_* in the environment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ref_showcase.resources import Resources

pytestmark = pytest.mark.live


def test_full_pipeline_to_ready(live_resources: Resources) -> None:
    res = live_resources
    if res.storage is None:
        pytest.skip("full pipeline needs MinIO (STORAGE_* env) — run against the full stack")

    from ref_showcase.pipeline import dispatch, submit_document
    from ref_showcase.resources import WORK_QUEUES

    doc_id, doc_key = submit_document(
        res,
        title="Fan-out demo",
        text="Alpha beta gamma.\nDelta epsilon zeta.\nEta theta iota.",
        tags=("demo",),
    )
    assert res.storage.exists(doc_key)  # raw body stored

    q = res.require_queue()
    # Drain every queue round-robin until all are empty (ingest fans out to embed + summarize).
    for _ in range(100):
        worked = False
        for name in WORK_QUEUES:
            worked = q.work_once(name, lambda job: dispatch(res, job)) or worked
        if not worked:
            break

    detail = res.require_repo().get_document(doc_id)
    assert detail is not None
    assert detail.summary.status == "ready"
    assert detail.summary.summary_key is not None
    assert detail.chunks  # was chunked
    assert all(c.has_embedding for c in detail.chunks)  # embed stage ran on every chunk
    assert res.storage.exists(detail.summary.summary_key)  # summary blob landed
    assert q.dead_letter_count() == 0  # nothing failed
