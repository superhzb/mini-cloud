# `mini-cloud-db`

Postgres connection, a small SQL migration runner, and a **job-queue primitive** — the one shared
implementation that retires the four bespoke SQLite + WAL + single-writer + job-queue stacks in the
workspace. The seam is the plain Postgres wire protocol (`DATABASE_URL`); nothing here hides it.

```python
from mini_cloud.config import load_settings
from mini_cloud.db import make_pool, migrate, JobQueue

pool = make_pool(load_settings().require("database_url"))
migrate(pool, "migrations")  # apply this app's NNNN_*.sql files, in order, once each

q = JobQueue(pool)
q.create_schema()  # queue tables (idempotent)
q.enqueue("resize", {"image": "a.png"}, dedupe_key="a.png")
q.run_worker("resize", handle_one)  # at-least-once; handler MUST be idempotent
```

## Job-queue semantics (the contract consumers inherit)

Specified before `db` is pinned `1.0`, because pinning means inheriting these:

| Aspect | Guarantee |
|---|---|
| **Delivery** | **At-least-once.** `dequeue` reserves via `SELECT … FOR UPDATE SKIP LOCKED`. A crashed worker's job is redelivered once its visibility deadline lapses. Handlers must be idempotent. |
| **Visibility timeout** | On dequeue, the job is hidden for `visibility_timeout` seconds. Long handlers call `extend()` to heartbeat. |
| **Retry / backoff** | Each delivery increments `attempts`; `nack` reschedules at `now() + default_backoff(attempts)` (exponential, capped). |
| **Dead-letter** | At `attempts >= max_attempts` a failed job moves to `mini_cloud_dead_letter` (kept, inspectable, replayable). |
| **Idempotent enqueue** | An optional `dedupe_key` keeps at most one live job per `(queue, key)` via a partial unique index. |
| **Ordering** | Best-effort `priority DESC, vt, id`; `SKIP LOCKED` trades strict order for throughput. |

Single-table design (one row per job, many named queues by the `queue` column) following the pgmq
pattern — chosen over hand-rolling a fifth queue.

## Migrations

Plain, ordered `NNNN_description.sql` files applied once each, tracked in `mini_cloud_migrations`.
Not an ORM. `migrate(source, dir)` is idempotent and returns the versions applied this call.

## Tests

`pytest` runs the pure unit tests with no services. The queue's behavioural spec lives in
`tests/test_queue_live.py` (marked `live`) — run against a **throwaway** Postgres:

```bash
DATABASE_URL=postgresql://postgres:pw@127.0.0.1:15432/postgres pytest --run-live
```
