"""`mini score <repo>` — score a repo 0–7 against the readiness scorecard.

Each metric is a mechanical, pass/fail check (see docs/MINI_CLOUD_ARCHITECTURE.md → *Scorecard*).
It is honest by construction: a freshly scaffolded app scores 7/7, an unmodified legacy repo scores
lower and says exactly which metrics fail and why. This checker touches only the target repo (never
modifies it) — it is the self-service tool a repo owner runs during adoption.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

METRICS = (
    "bootstrap_self_sufficiency",
    "task_entrypoints",
    "validation_harness",
    "lint_format_gates",
    "agent_repo_map",
    "structured_docs",
    "observability_wired",
)


@dataclass(frozen=True, slots=True)
class Check:
    metric: str
    passed: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Scorecard:
    repo: Path
    checks: tuple[Check, ...]

    @property
    def score(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total(self) -> int:
        return len(self.checks)


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="ignore")
    except OSError:
        return ""


def _makefile_targets(repo: Path) -> set[str]:
    text = _read(repo / "Makefile") + _read(repo / "makefile") + _read(repo / "justfile")
    targets: set[str] = set()
    for line in text.splitlines():
        if ":" in line and not line.startswith(("\t", " ", "#")):
            name = line.split(":", 1)[0].strip()
            if name and all(ch.isalnum() or ch in "-_" for ch in name):
                targets.add(name)
    return targets


_LOCKFILES = ("uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml", "requirements.lock")


def _has_lockfile(repo: Path) -> bool:
    if any((repo / f).is_file() for f in _LOCKFILES):
        return True
    # A uv-workspace member is still bootstrap-self-sufficient via the workspace-root lock; look a
    # few levels up for it (bounded, so an unrelated distant lock can't false-pass a legacy repo).
    for ancestor in list(repo.resolve().parents)[:3]:
        if (ancestor / "uv.lock").is_file():
            return True
    return False


def _grep_repo(repo: Path, needles: tuple[str, ...], *, exts: tuple[str, ...]) -> bool:
    for path in repo.rglob("*"):
        if path.is_file() and path.suffix in exts and not _skip(path):
            text = _read(path)
            if any(n in text for n in needles):
                return True
    return False


def _skip(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & {".git", "node_modules", ".venv", "__pycache__", "dist", "build"})


def score_repo(repo: Path) -> Scorecard:
    repo = Path(repo)
    targets = _makefile_targets(repo)
    pyproject = _read(repo / "pyproject.toml")

    checks: list[Check] = []

    # 1 — bootstrap: pinned deps + .env.example + one documented setup command
    env_example = (repo / ".env.example").is_file()
    lock = _has_lockfile(repo)
    setup_cmd = "setup" in targets or "bootstrap" in targets
    checks.append(
        Check(
            "bootstrap_self_sufficiency",
            env_example and lock and setup_cmd,
            _reason(
                (".env.example", env_example),
                ("lockfile", lock),
                ("make setup/bootstrap", setup_cmd),
            ),
        )
    )

    # 2 — task entrypoints: canonical target names
    required = {"run", "test", "lint", "check"}
    have = required & targets
    checks.append(
        Check(
            "task_entrypoints",
            required <= targets,
            f"targets present: {sorted(have)}; missing: {sorted(required - targets)}",
        )
    )

    # 3 — validation harness: a `check` gate, ideally against an ephemeral DB
    check_gate = "check" in targets
    ephemeral = "check-live" in targets or "ephemeral" in _read(repo / "Makefile").lower()
    checks.append(
        Check(
            "validation_harness",
            check_gate,
            _reason(("make check", check_gate), ("ephemeral-DB path", ephemeral)),
        )
    )

    # 4 — lint/format gates: shared config referenced + a lint target
    shared_ruff = "ruff-base" in pyproject or (repo / ".ruff.toml").is_file() or "ruff" in pyproject
    shared_pyright = "pyright-base" in _read(repo / "pyrightconfig.json") or "pyright" in pyproject
    lint_target = "lint" in targets
    checks.append(
        Check(
            "lint_format_gates",
            shared_ruff and lint_target,
            _reason(
                ("shared ruff config", shared_ruff),
                ("shared pyright config", shared_pyright),
                ("make lint", lint_target),
            ),
        )
    )

    # 5 — agent repo map
    agents = (repo / "AGENTS.md").is_file()
    checks.append(Check("agent_repo_map", agents, _reason(("AGENTS.md", agents))))

    # 6 — structured docs
    readme = (repo / "README.md").is_file()
    docs_dir = (repo / "docs").is_dir()
    checks.append(
        Check(
            "structured_docs",
            readme and docs_dir and env_example,
            _reason(("README.md", readme), ("docs/", docs_dir), (".env.example", env_example)),
        )
    )

    # 7 — observability wired by default
    obs = _grep_repo(
        repo,
        ("mini_cloud.obs", "obs.install", "@mini-cloud/obs", "/metrics"),
        exts=(".py", ".ts", ".tsx", ".js"),
    )
    checks.append(Check("observability_wired", obs, _reason(("obs SDK / /metrics wired", obs))))

    return Scorecard(repo=repo, checks=tuple(checks))


def _reason(*parts: tuple[str, bool]) -> str:
    return ", ".join(f"{'✓' if ok else '✗'} {label}" for label, ok in parts)


def format_scorecard(card: Scorecard) -> str:
    lines = [f"scorecard for {card.repo}  →  {card.score}/{card.total}", ""]
    for c in card.checks:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{mark}] {c.metric:<28} {c.reason}")
    return "\n".join(lines)
