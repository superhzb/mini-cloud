"""The demo job handler: load a note from storage, summarise it (inference or trivial fallback),
write the summary back. Idempotent (the queue is at-least-once). Replace with your own work."""

from __future__ import annotations

from mini_cloud.db import Job
from mini_cloud.obs import get_logger

from .resources import Resources

_log = get_logger("{{package}}.tasks")

SUMMARY_SYSTEM = "You summarise text in one short sentence. Output only the summary."


def summary_key(note_key: str) -> str:
    return f"{note_key}.summary.txt"


def handle_note(res: Resources, job: Job) -> None:
    storage = res.require_storage()
    note_key = job.payload["note_key"]
    text = storage.get_bytes(note_key).decode("utf-8")

    if res.inference is not None and res.inference.default_model:
        summary = res.inference.chat(text, system=SUMMARY_SYSTEM, max_tokens=64)
    else:
        summary = text.strip().split("\n", 1)[0][:140]

    storage.put_bytes(summary_key(note_key), summary.encode("utf-8"), content_type="text/plain")
    _log.info("summarised note", extra={"note_key": note_key, "chars_out": len(summary)})
