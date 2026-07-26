# `mini-cloud-obs`

Structured logs → Loki, metrics → Prometheus, correlation-ID propagation — **on by default**
(no opt-in flag; that's scorecard metric #7). Retires per-app logging env and scattered JSONL
directories with one shared pane of glass (the infra stack's Grafana).

```python
from mini_cloud.config import load_settings
from mini_cloud.obs import get_logger, bind_correlation_id
from mini_cloud.obs.asgi import install  # pip install mini-cloud-obs[asgi]

settings = load_settings()
install(app, settings)  # JSON logging (+Loki if LOKI_URL), request metrics, GET /metrics
log = get_logger(__name__)
log.info("started", extra={"port": settings.port})

with bind_correlation_id():  # correlate a non-HTTP unit of work (a job handler)
    handle_job()
```

## What you get

| Piece | Behaviour |
|---|---|
| **JSON logs** | one JSON object per line (`ts`, `level`, `logger`, `msg`, `app`, `env`, `correlation_id`, +extras) to stdout, always. |
| **Loki push** | if `LOKI_URL` is set, a background-batching `LokiHandler` also ships lines to Loki. Best-effort — never blocks or crashes the app. |
| **Correlation IDs** | a contextvar, auto-isolated per async-task/thread. The ASGI middleware reads/sets `X-Correlation-ID` and echoes it back. |
| **Metrics** | `http_requests_total` + `http_request_duration_seconds` labelled by method / matched-route / status; exposed at `/metrics` for Prometheus to scrape (scrape-first). |
| **`install(app, settings)`** | one call wires all of the above into a Starlette/FastAPI app. |

Labels use the **matched route template** (`/items/{id}`), never the raw path, to keep cardinality
bounded. Add domain metrics with plain `prometheus_client`; this package only standardises the
request-level ones and the exposition endpoint.
