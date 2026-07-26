"""Funnel and retention SQL — the query side, where identity is resolved.

Shipped in the package so apps don't reinvent the hard part. Both queries resolve anonymous ->
identified at read time by LEFT JOINing ``analytics_person_aliases`` and collapsing to one *person*
key (``coalesce(alias.distinct_id, event.distinct_id)``) — the decision recorded in the plan that
keeps the write path a dumb append.

- **Funnel:** per person, ``min(timestamp) FILTER (WHERE event = …)`` for each step, then count the
  persons who reached each step *in order* (step N requires a step-N time at/after step N-1).
- **Retention:** weekly cohorts anchored on a first event, counting distinct persons active in each
  subsequent week.

The ``*_sql`` builders are pure strings (unit-testable offline); the ``run_*`` helpers bind
parameters and execute against a :data:`~mini_cloud.db.ConnSource`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mini_cloud.db import ConnSource, acquire

if TYPE_CHECKING:
    from collections.abc import Sequence

# Shared: resolve every event to a single person key via the alias map. `%s` binds the project.
_RESOLVED_CTE = """
    WITH resolved AS (
        SELECT coalesce(a.distinct_id, e.distinct_id) AS person,
               e.event AS event,
               e.timestamp AS ts
        FROM analytics_events e
        LEFT JOIN analytics_person_aliases a ON a.previous_id = e.distinct_id
        WHERE e.project = %s
    )
"""


@dataclass(frozen=True, slots=True)
class FunnelStep:
    """One step of a funnel result: how many persons reached it and the conversion ratios."""

    event: str
    count: int
    conversion_from_prev: float  # count / previous step's count (1.0 for the first step)
    conversion_from_top: float  # count / first step's count


@dataclass(frozen=True, slots=True)
class FunnelResult:
    """A completed funnel: per-step counts plus the end-to-end conversion."""

    steps: list[FunnelStep]
    entered: int  # persons at step 1
    converted: int  # persons who completed the last step in order
    overall_conversion: float  # converted / entered


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Weekly retention: for each ``(cohort_week, period)`` how many persons were active.

    ``period`` is the number of weeks after the cohort week (0 = the anchor week itself). Ship the
    flat cells; a dashboard pivots them into the familiar triangle.
    """

    anchor_event: str
    cells: list[tuple[str, int, int]]  # (cohort_week_iso, period, active_persons)


def funnel_sql(steps: Sequence[str]) -> str:
    """Build the funnel SQL for ``len(steps)`` ordered steps.

    Parameter order when executed: ``(project, step0, step1, …)`` — the project binds the
    ``resolved`` CTE, then one event-name parameter per step (in order) binds the per-step
    ``min(...) FILTER`` columns.
    """
    if not steps:
        raise ValueError("a funnel needs at least one step")
    n = len(steps)
    first_cols = ",\n               ".join(
        f"min(ts) FILTER (WHERE event = %s) AS s{i}" for i in range(n)
    )
    count_cols = []
    for i in range(n):
        conds = [f"s{j} IS NOT NULL" for j in range(i + 1)]
        conds += [f"s{j} >= s{j - 1}" for j in range(1, i + 1)]
        count_cols.append(f"count(*) FILTER (WHERE {' AND '.join(conds)}) AS c{i}")
    count_select = ",\n           ".join(count_cols)
    return f"""{_RESOLVED_CTE},
    firsts AS (
        SELECT person,
               {first_cols}
        FROM resolved
        GROUP BY person
    )
    SELECT {count_select}
    FROM firsts
    """


def run_funnel(source: ConnSource, steps: Sequence[str], *, project: str) -> FunnelResult:
    """Execute the ordered funnel for ``project`` and return per-step counts + conversion."""
    steps = list(steps)
    with acquire(source) as conn:
        # SQL is assembled from static fragments (fixed column templates); every runtime value
        # (project, event names) is a bound parameter, so this is not string-interpolated input.
        row = conn.execute(funnel_sql(steps), (project, *steps)).fetchone()  # type: ignore[arg-type]
    counts = [int(x) for x in row] if row else [0] * len(steps)
    top = counts[0] if counts else 0
    result_steps: list[FunnelStep] = []
    prev: int | None = None
    for event, count in zip(steps, counts, strict=True):
        result_steps.append(
            FunnelStep(
                event=event,
                count=count,
                conversion_from_prev=(count / prev) if prev else 1.0,
                conversion_from_top=(count / top) if top else 0.0,
            )
        )
        prev = count
    converted = counts[-1] if counts else 0
    return FunnelResult(
        steps=result_steps,
        entered=top,
        converted=converted,
        overall_conversion=(converted / top) if top else 0.0,
    )


def retention_sql() -> str:
    """Build weekly-cohort retention SQL.

    Parameter order when executed: ``(project, anchor_event, anchor_event, periods)`` — project
    binds ``resolved``; the anchor event binds the cohort's first-touch filter twice (the
    ``min(...) FILTER`` and its ``HAVING`` guard); ``periods`` caps how many weeks out to report.
    """
    return f"""{_RESOLVED_CTE},
    cohorts AS (
        SELECT person,
               date_trunc('week', min(ts) FILTER (WHERE event = %s)) AS cohort_week
        FROM resolved
        GROUP BY person
        HAVING min(ts) FILTER (WHERE event = %s) IS NOT NULL
    ),
    activity AS (
        SELECT DISTINCT c.person,
               c.cohort_week,
               (extract(epoch FROM (date_trunc('week', r.ts) - c.cohort_week)) / 604800)::int
                   AS period
        FROM cohorts c
        JOIN resolved r ON r.person = c.person
        WHERE r.ts >= c.cohort_week
    )
    SELECT to_char(cohort_week, 'YYYY-MM-DD') AS cohort_week,
           period,
           count(DISTINCT person) AS active
    FROM activity
    WHERE period BETWEEN 0 AND %s
    GROUP BY cohort_week, period
    ORDER BY cohort_week, period
    """


def run_retention(
    source: ConnSource, anchor_event: str, *, project: str, periods: int = 8
) -> RetentionResult:
    """Execute weekly-cohort retention anchored on ``anchor_event`` for ``project``."""
    with acquire(source) as conn:
        rows = conn.execute(
            retention_sql(),  # type: ignore[arg-type]  # static SQL; all values bound below
            (project, anchor_event, anchor_event, periods),
        ).fetchall()
    cells = [(str(r[0]), int(r[1]), int(r[2])) for r in rows]
    return RetentionResult(anchor_event=anchor_event, cells=cells)
