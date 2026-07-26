"""Tests for the scorecard checker."""

from __future__ import annotations

from pathlib import Path

from mini_scaffolder.score import score_repo


def _make_perfect_repo(root: Path) -> None:
    (root / "docs").mkdir()
    (root / "src").mkdir()
    (root / ".env.example").write_text("APP_NAME=x\n")
    (root / "uv.lock").write_text("# lock\n")
    (root / "AGENTS.md").write_text("# map\n")
    (root / "README.md").write_text("# readme\n")
    (root / "docs" / "scorecard.md").write_text("scores\n")
    (root / "pyrightconfig.json").write_text(
        '{"extends": "../mini-cloud/tooling/pyright-base.json"}'
    )
    (root / "pyproject.toml").write_text(
        '[tool.ruff]\nextend = "../mini-cloud/tooling/ruff-base.toml"\n'
    )
    (root / "src" / "app.py").write_text("from mini_cloud.obs.asgi import install\n")
    (root / "Makefile").write_text(
        "setup:\n\techo\nrun:\n\techo\ntest:\n\techo\nlint:\n\techo\n"
        "check:\n\techo\ncheck-live:\n\techo\n"
    )


def test_perfect_repo_scores_7(tmp_path) -> None:
    _make_perfect_repo(tmp_path)
    card = score_repo(tmp_path)
    failing = [c.metric for c in card.checks if not c.passed]
    assert card.score == 7, f"expected 7/7, failing: {failing}"


def test_empty_repo_scores_low(tmp_path) -> None:
    card = score_repo(tmp_path)
    assert card.score == 0
    assert all(not c.passed for c in card.checks)


def test_missing_lockfile_fails_bootstrap(tmp_path) -> None:
    _make_perfect_repo(tmp_path)
    (tmp_path / "uv.lock").unlink()
    card = score_repo(tmp_path)
    boot = next(c for c in card.checks if c.metric == "bootstrap_self_sufficiency")
    assert boot.passed is False
    assert card.score == 6


def test_missing_obs_fails_observability(tmp_path) -> None:
    _make_perfect_repo(tmp_path)
    (tmp_path / "src" / "app.py").write_text("print('no obs here')\n")
    card = score_repo(tmp_path)
    obs = next(c for c in card.checks if c.metric == "observability_wired")
    assert obs.passed is False
