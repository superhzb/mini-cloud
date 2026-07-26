"""The background worker: drains the ``summarize`` queue and runs the handler.

Run as its own process (``ref-fastapi-worker`` / ``make worker``), separate from the web process.
Each job runs inside a fresh correlation ID so its logs are traceable in Grafana just like a
request. At-least-once delivery means the handler must be idempotent — it is (see ``tasks.py``).
"""

from __future__ import annotations

import signal
import sys
from types import FrameType

from mini_cloud.db import Job
from mini_cloud.obs import bind_correlation_id, configure_logging, get_logger

from .resources import SUMMARIZE_QUEUE, build_resources
from .tasks import handle_summarize

_stop = False


def _request_stop(_sig: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


def main() -> int:
    res = build_resources()
    configure_logging(res.settings)
    log = get_logger("ref_fastapi.worker")
    queue = res.require_queue()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    def handler(job: Job) -> None:
        with bind_correlation_id():  # correlate this job's logs end-to-end
            handle_summarize(res, job)

    log.info("worker starting", extra={"queue": SUMMARIZE_QUEUE})
    queue.run_worker(
        SUMMARIZE_QUEUE,
        handler,
        worker_id="ref-fastapi-worker",
        stop=lambda: _stop,
    )
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
