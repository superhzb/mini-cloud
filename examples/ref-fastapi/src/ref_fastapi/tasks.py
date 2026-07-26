"""The demo job handler that threads storage + inference together.

Flow: the API stores a note's text in the bucket and enqueues a ``summarize`` job carrying the
object key. This handler loads the text, produces a summary (via the inference gateway when one is
configured, else a trivial fallback so the demo runs without MLX), and writes the summary back to
storage. Because delivery is at-least-once, the handler is **idempotent** — re-running it just
overwrites the same summary key.
"""

from __future__ import annotations

from mini_cloud.db import Job
from mini_cloud.obs import get_logger

from .resources import Resources

_log = get_logger("ref_fastapi.tasks")

SUMMARY_SYSTEM = "You summarise text in one short sentence. Output only the summary."


def summary_key(note_key: str) -> str:
    """Deterministic derived key — idempotent overwrite target for a note's summary."""
    return f"{note_key}.summary.txt"


def handle_summarize(res: Resources, job: Job) -> None:
    """Process one ``summarize`` job. Raises on unrecoverable input so the queue can retry /
    dead-letter; writes the summary object on success."""
    storage = res.require_storage()
    note_key = job.payload["note_key"]
    text = storage.get_bytes(note_key).decode("utf-8")  # KeyError -> retry/dead-letter

    if res.inference is not None and res.inference.default_model:
        summary = res.inference.chat(text, system=SUMMARY_SYSTEM, max_tokens=64)
    else:
        # Fallback keeps the reference app runnable without a live gateway.
        summary = text.strip().split("\n", 1)[0][:140]

    storage.put_bytes(summary_key(note_key), summary.encode("utf-8"), content_type="text/plain")
    _log.info(
        "summarised note",
        extra={"note_key": note_key, "chars_in": len(text), "chars_out": len(summary)},
    )
