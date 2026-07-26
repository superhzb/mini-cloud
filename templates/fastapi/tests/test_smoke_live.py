"""End-to-end smoke test against real infra (marked ``live``). Proves the notes→summary flow on
shared Postgres + MinIO through the SDK. Needs canonical env + --run-live."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from {{package}}.app import create_app
from {{package}}.resources import WORK_QUEUE, Resources
from {{package}}.tasks import handle_note

pytestmark = pytest.mark.live


def test_notes_to_summary() -> None:
    app = create_app()
    with TestClient(app) as client:
        res: Resources = app.state.resources
        assert res.queue is not None and res.storage is not None
        assert client.get("/readyz").json()["ready"] is True

        r = client.post("/notes", json={"text": "first line.\nsecond line."})
        assert r.status_code == 202
        note_id = r.json()["note_key"].removeprefix("notes/").removesuffix(".txt")

        assert client.get(f"/notes/{note_id}/summary").status_code == 404
        assert res.queue.work_once(WORK_QUEUE, lambda job: handle_note(res, job)) is True

        got = client.get(f"/notes/{note_id}/summary")
        assert got.status_code == 200
        assert got.json()["summary"]
