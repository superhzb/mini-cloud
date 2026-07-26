"""End-to-end smoke test against the real infra stack (marked ``live``).

Proves the platform acceptance path: the app runs on shared Postgres + MinIO through the SDK, uses
the SDK job queue, and the notes→summary flow completes. Needs canonical env (DATABASE_URL +
STORAGE_*) and `--run-live`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ref_fastapi.app import create_app
from ref_fastapi.resources import SUMMARIZE_QUEUE, Resources
from ref_fastapi.tasks import handle_summarize

pytestmark = pytest.mark.live


def test_notes_to_summary_end_to_end() -> None:
    app = create_app()
    with TestClient(app) as client:  # triggers lifespan → build_resources (migrate, schema, bucket)
        res: Resources = app.state.resources
        assert res.queue is not None and res.storage is not None

        # readiness should be green with real infra
        assert client.get("/readyz").json()["ready"] is True

        # submit a note
        r = client.post("/notes", json={"text": "mini-cloud starts demos fast.\nSecond line."})
        assert r.status_code == 202
        note_id = r.json()["note_key"].removeprefix("notes/").removesuffix(".txt")

        # summary not ready yet
        assert client.get(f"/notes/{note_id}/summary").status_code == 404
        assert res.queue.depth(SUMMARIZE_QUEUE)["total"] >= 1

        # drain one job through the real handler
        processed = res.queue.work_once(SUMMARIZE_QUEUE, lambda job: handle_summarize(res, job))
        assert processed is True

        # summary now available
        got = client.get(f"/notes/{note_id}/summary")
        assert got.status_code == 200
        assert got.json()["summary"]
