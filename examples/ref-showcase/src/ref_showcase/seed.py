"""Generate and ingest a deterministic, network-free demonstration corpus.

``make seed`` always uses the deterministic fallback embedding and summary, even if a gateway URL
is present in ``.env``. ``make seed-live`` opts into the configured gateway. Both commands need
the app's Postgres database and object-storage bucket because they run the real upload → queue →
embed → summarize pipeline.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from mini_cloud.analytics import Event, PostgresSink
from mini_cloud.db import ConnSource, acquire
from mini_cloud.obs import bind_correlation_id, get_logger

from .analytics_tour import EVENT_SEARCH_PERFORMED, FUNNEL_STEPS
from .pipeline import dispatch, submit_document
from .resources import PIPELINE_QUEUES, Resources, build_resources, embed_model

CORPUS_SEED = 20260725
CORPUS_SIZE = 48

# Analytics event stream: synthetic people whose journeys drop off through the 4-step funnel, with
# backdated timestamps spread over several weeks so funnel *and* retention dashboards have data with
# no live gateway. Deterministic and re-runnable (the seed replaces its own rows).
ANALYTICS_SEED_USERS = 40
ANALYTICS_COHORT_WEEKS = 6
# A fixed anchor (never `now()`) keeps the stream fully reproducible; the dashboard uses a wide
# absolute/relative range to see it.
_ANALYTICS_BASE = datetime(2026, 6, 8, 9, 0, tzinfo=UTC)
# Conditional continuation probability at each step boundary (step0->1, 1->2, 2->3).
_STEP_CONTINUE = (0.72, 0.6, 0.5)

_log = get_logger("ref_showcase.seed")

_TOPICS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "Postgres queues",
        ("database", "queue"),
        (
            "visibility timeouts prevent two workers from owning the same delivery window",
            "dedupe keys make repeated producer requests idempotent",
            "dead letters preserve failed work for an operator to replay",
            "priority and delay control when otherwise independent jobs become visible",
        ),
    ),
    (
        "Object storage",
        ("storage", "s3"),
        (
            "namespaced object keys keep raw documents, chunks, and summaries easy to inspect",
            "presigned URLs let browsers transfer large objects without proxying through the app",
            "the same S3 contract targets local MinIO and managed storage",
            "streaming uploads avoid buffering an entire file in application memory",
        ),
    ),
    (
        "Semantic search",
        ("inference", "search"),
        (
            "query and corpus vectors must come from the same embedding model",
            "cosine similarity is sufficient for a small portable demonstration corpus",
            "dimension checks prevent mixed embedding models from producing misleading rankings",
            "a deterministic fallback keeps local development useful without a model gateway",
        ),
    ),
    (
        "Observability",
        ("observability", "operations"),
        (
            "request metrics describe transport health while business metrics describe outcomes",
            "correlation identifiers travel inside queue payloads across process boundaries",
            "structured fields make worker and request logs searchable in one Loki view",
            "low-cardinality labels keep Prometheus queries predictable",
        ),
    ),
    (
        "Configuration",
        ("configuration", "portability"),
        (
            "canonical environment names let an app move between laptop and server unchanged",
            "typed settings turn missing dependencies into clear startup diagnostics",
            "secrets stay in environment configuration and never appear in debug responses",
            "one project identifier consistently labels inference traffic",
        ),
    ),
    (
        "Reliable pipelines",
        ("pipeline", "reliability"),
        (
            "at-least-once delivery requires handlers whose durable effects can safely repeat",
            "fan-out lets embedding and summarization progress independently",
            "heartbeats extend visibility while legitimately long work is still running",
            "offline fallbacks preserve the end-to-end development loop",
        ),
    ),
)

_AUDIENCES = (
    "a developer evaluating the reference app",
    "an operator diagnosing a local stack",
    "a team moving a prototype toward production",
    "an engineer writing an idempotent worker",
)

_CONCLUSIONS = (
    "The practical lesson is to keep the service boundary explicit and observable.",
    "This keeps the example small while preserving the production contract.",
    "The result is reproducible locally and replaceable by configuration later.",
    "That trade-off favors a clear reference over hidden framework behavior.",
)


@dataclass(frozen=True, slots=True)
class SeedDocument:
    title: str
    text: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SeedResult:
    """Structured result shared by the HTTP endpoint and the CLI wrapper."""

    requested: int
    created: int
    skipped: int
    jobs_processed: int
    analytics_events: int
    analytics: str
    mode: str


def generate_corpus(*, seed: int = CORPUS_SEED) -> list[SeedDocument]:
    """Return the same 48 short documents for a given seed, without reading network or services."""
    rng = random.Random(seed)
    documents: list[SeedDocument] = []
    for topic_index, (topic, tags, facts) in enumerate(_TOPICS):
        for variant in range(CORPUS_SIZE // len(_TOPICS)):
            selected = list(facts)
            rng.shuffle(selected)
            audience = rng.choice(_AUDIENCES)
            conclusion = rng.choice(_CONCLUSIONS)
            title = f"{topic}: field note {variant + 1}"
            text = (
                f"This field note explains {topic.lower()} for {audience}.\n"
                f"First, {selected[0]}. Next, {selected[1]}.\n"
                f"A useful cross-check is that {selected[2]}. {conclusion}"
            )
            documents.append(
                SeedDocument(
                    title=title,
                    text=text,
                    tags=(*tags, f"series-{topic_index + 1}"),
                )
            )
    return documents


def _existing_seed_titles(res: Resources) -> set[str]:
    repo = res.require_repo()
    titles: set[str] = set()
    offset = 0
    while True:
        page = repo.list_documents(limit=100, offset=offset)
        if not page:
            return titles
        titles.update(item.title for item in page if item.source == "seed")
        offset += len(page)


def _drain_pipeline(res: Resources, queues: tuple[str, ...] = PIPELINE_QUEUES) -> int:
    queue = res.require_queue()
    processed = 0
    while True:
        worked = False
        for name in queues:
            did_work = queue.work_once(name, lambda job: dispatch(res, job))
            worked = did_work or worked
            processed += int(did_work)
        if not worked:
            return processed


def generate_event_stream(
    project: str, *, seed: int = CORPUS_SEED
) -> tuple[list[Event], list[tuple[str, dict[str, object]]], list[tuple[str, str]]]:
    """Build a deterministic ``(events, persons, aliases)`` stream for ``project`` — no DB, no
    network. Most people enter the funnel and drop off; a handful start anonymous and are then
    aliased to an identified id, so query-time identity resolution has something real to collapse.
    """
    rng = random.Random(seed + 7)
    events: list[Event] = []
    persons: list[tuple[str, dict[str, object]]] = []
    aliases: list[tuple[str, str]] = []

    for u in range(ANALYTICS_SEED_USERS):
        distinct_id = f"seed-user-{u:03d}"
        cohort = u % ANALYTICS_COHORT_WEEKS
        base = _ANALYTICS_BASE + timedelta(weeks=cohort, hours=rng.randint(0, 60))

        # How far this person gets: always step 0, then each further step is conditional.
        reached = 1
        for i in range(1, len(FUNNEL_STEPS)):
            if rng.random() < _STEP_CONTINUE[i - 1]:
                reached += 1
            else:
                break

        # Every ~fifth person starts anonymous and identifies partway (alias stitch).
        split = u % 5 == 0
        anon_id = f"anon-{distinct_id}" if split else None
        if split:
            persons.append((distinct_id, {"plan": "pro" if u % 2 else "free", "cohort": cohort}))
            aliases.append((anon_id or "", distinct_id))

        for i in range(reached):
            ts = base + timedelta(minutes=sum(rng.randint(3, 40) for _ in range(i + 1)))
            # Pre-alias steps fire under the anonymous id; later steps under the identified id.
            actor = anon_id if (split and i < 2) else distinct_id
            events.append(
                Event(
                    event=FUNNEL_STEPS[i],
                    distinct_id=actor or distinct_id,
                    project=project,
                    properties={"step": i, "seeded": True},
                    timestamp=ts,
                )
            )

        # Returning activity a week or two later feeds the retention cohorts.
        if reached >= 2 and rng.random() < 0.55:
            back = base + timedelta(weeks=rng.randint(1, 3), hours=rng.randint(0, 40))
            events.append(
                Event(
                    event=EVENT_SEARCH_PERFORMED,
                    distinct_id=distinct_id,
                    project=project,
                    properties={"returning": True, "seeded": True},
                    timestamp=back,
                )
            )

    return events, persons, aliases


_DELETE_SEEDED_EVENTS_SQL = (
    "DELETE FROM analytics_events WHERE project = %s AND (properties->>'seeded') = 'true'"
)


def seed_analytics_events(pool: ConnSource, project: str, *, seed: int = CORPUS_SEED) -> int:
    """Replace this project's seeded events with a fresh deterministic stream. Returns event count.

    Idempotent: prior ``seeded`` events are removed first (persons/aliases upsert), so re-running is
    safe and always yields the same corpus. Writes through :class:`PostgresSink` — the same sink the
    live client uses — so the seed exercises the real write path.
    """
    events, persons, aliases = generate_event_stream(project, seed=seed)
    with acquire(pool) as conn:
        conn.execute(_DELETE_SEEDED_EVENTS_SQL, (project,))
        if not conn.autocommit:
            conn.commit()
    sink = PostgresSink(pool)
    for distinct_id, props in persons:
        sink.identify(distinct_id, props)
    for previous_id, distinct_id in aliases:
        sink.alias(previous_id, distinct_id)
    sink.write_events(events)
    _log.info(
        "analytics events seeded",
        extra={
            "project": project,
            "events": len(events),
            "persons": len(persons),
            "aliases": len(aliases),
        },
    )
    return len(events)


def seed_corpus(
    res: Resources,
    *,
    count: int = CORPUS_SIZE,
    live: bool = False,
    drain_queues: tuple[str, ...] = PIPELINE_QUEUES,
) -> SeedResult:
    """Seed ``count`` deterministic documents using caller-owned resources.

    The operation never closes resources it did not create. Existing titles are skipped, analytics
    is refreshed when configured, and only ``drain_queues`` are worked synchronously.
    """
    if not 1 <= count <= CORPUS_SIZE:
        raise ValueError(f"count must be between 1 and {CORPUS_SIZE}")
    res.require_repo()
    res.require_storage()
    res.require_queue()
    if live:
        if res.inference is None or not res.inference.default_model or embed_model() is None:
            raise RuntimeError(
                "seed-live requires MINI_INFERENCE_URL, INFERENCE_MODEL, and INFERENCE_EMBED_MODEL"
            )
    original_inference = res.inference
    if not live:
        # The offline target is a hard guarantee, not an accident of an unset model variable.
        res.inference = None

    try:
        selected = generate_corpus()[:count]
        existing = _existing_seed_titles(res)
        created = 0
        skipped = 0
        for index, document in enumerate(selected):
            if document.title in existing:
                skipped += 1
                continue
            stable_id = uuid5(NAMESPACE_URL, f"mini-cloud/ref-showcase/{document.title}")
            with bind_correlation_id(f"seed-{stable_id.hex[:12]}"):
                submit_document(
                    res,
                    title=document.title,
                    text=document.text,
                    source="seed",
                    tags=document.tags,
                    doc_key=f"docs/seed/{stable_id.hex}.txt",
                )
            created += 1
            _log.info(
                "seed document submitted",
                extra={"index": index + 1, "total": count, "title": document.title},
            )

        processed = _drain_pipeline(res, drain_queues)

        # Independent of the document pipeline; absence is a supported degraded state.
        events = 0
        analytics_status = "unavailable"
        if res.analytics_pool is not None and res.analytics is not None:
            events = seed_analytics_events(res.analytics_pool, res.analytics.project)
            analytics_status = "seeded"

        result = SeedResult(
            requested=count,
            created=created,
            skipped=skipped,
            jobs_processed=processed,
            analytics_events=events,
            analytics=analytics_status,
            mode="gateway" if live else "offline-fallback",
        )
        _log.info(
            "corpus ready",
            extra={
                "requested": result.requested,
                "documents_created": result.created,
                "documents_skipped": result.skipped,
                "jobs_processed": result.jobs_processed,
                "analytics_events": result.analytics_events,
            },
        )
        return result
    finally:
        res.inference = original_inference


def _seed_owned_resources(*, live: bool = False) -> SeedResult:
    """CLI ownership wrapper: build once, seed the full corpus, and close what it built."""
    res = build_resources()
    try:
        return seed_corpus(res, live=live)
    finally:
        if res.analytics is not None:
            res.analytics.close()
        if res.analytics_pool is not None and hasattr(res.analytics_pool, "close"):
            res.analytics_pool.close()
        if res.pool is not None and hasattr(res.pool, "close"):
            res.pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the configured inference gateway instead of deterministic fallbacks",
    )
    args = parser.parse_args(argv)
    result = _seed_owned_resources(live=args.live)
    mode = "gateway" if args.live else "offline fallback"
    print(
        f"corpus ready ({mode}): {result.created} created, "
        f"{result.jobs_processed} jobs processed, {result.analytics_events} analytics events"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
