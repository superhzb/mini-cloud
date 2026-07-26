"""Background worker: drains the work queue. Run as its own process (`make worker`). Each job runs
under a fresh correlation ID so its logs trace in Grafana. Handlers must be idempotent."""

from __future__ import annotations

import signal
import sys
from types import FrameType

from mini_cloud.db import Job
from mini_cloud.obs import bind_correlation_id, configure_logging, get_logger

from .resources import WORK_QUEUE, build_resources
from .tasks import handle_note

_stop = False


def _request_stop(_sig: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


def main() -> int:
    res = build_resources()
    configure_logging(res.settings)
    log = get_logger("{{package}}.worker")
    queue = res.require_queue()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    def handler(job: Job) -> None:
        with bind_correlation_id():
            handle_note(res, job)

    log.info("worker starting", extra={"queue": WORK_QUEUE})
    queue.run_worker(WORK_QUEUE, handler, worker_id="{{name}}-worker", stop=lambda: _stop)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
