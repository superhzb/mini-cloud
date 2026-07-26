"""The background worker — drains all showcase queues and dispatches each job to its handler.

Run as its own process (``ref-showcase-worker`` / ``make worker``), separate from the web process.
The SDK's :meth:`JobQueue.run_worker` drives a single queue; the showcase fans out across several,
so this loop round-robins :meth:`JobQueue.work_once` over :data:`WORK_QUEUES` and sleeps only when
*every* queue is empty. Each job runs under its carried correlation id (see ``pipeline.dispatch``),
so a worker log line traces back to the request that enqueued the work — visible in Grafana.
"""

from __future__ import annotations

import signal
import sys
import time
from types import FrameType

from mini_cloud.obs import configure_logging, get_logger

from .pipeline import dispatch
from .resources import WORK_QUEUES, build_resources

_stop = False


def _request_stop(_sig: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


def main() -> int:
    res = build_resources()
    configure_logging(res.settings)
    log = get_logger("ref_showcase.worker")
    queue = res.require_queue()

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    log.info("worker starting", extra={"queues": list(WORK_QUEUES)})
    while not _stop:
        worked = False
        for q in WORK_QUEUES:
            # work_once reserves one job and runs dispatch (ack on success, nack/dead-letter on
            # error), returning whether anything ran. OR across queues so none starves the others.
            worked = queue.work_once(q, lambda job: dispatch(res, job)) or worked
        if not worked:
            time.sleep(1.0)
    log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
