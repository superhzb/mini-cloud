"""Tests for the template renderer."""

from __future__ import annotations

import pytest

from mini_scaffolder.render import render_tree, substitute


def test_substitute_replaces_double_braces() -> None:
    assert substitute("hello {{name}}", {"name": "x"}) == "hello x"


def test_substitute_leaves_single_braces_untouched() -> None:
    # FastAPI path params like /{id} must pass through.
    assert substitute("/items/{id}", {}) == "/items/{id}"


def test_substitute_unknown_var_raises() -> None:
    with pytest.raises(KeyError, match="missing"):
        substitute("{{missing}}", {})


def test_render_tree_substitutes_paths_and_content(tmp_path) -> None:
    tpl = tmp_path / "tpl"
    (tpl / "src" / "{{package}}").mkdir(parents=True)
    (tpl / "src" / "{{package}}" / "app.py").write_text("name = '{{name}}'\n")
    (tpl / "gitignore.tmpl").write_text(".env\n")
    dest = tmp_path / "out"

    written = render_tree(tpl, dest, {"package": "demo_x", "name": "demo-x"})

    assert (dest / "src" / "demo_x" / "app.py").read_text() == "name = 'demo-x'\n"
    assert (dest / "gitignore").is_file()  # .tmpl suffix dropped
    assert any(p.name == "app.py" for p in written)


def test_render_tree_refuses_nonempty_dest(tmp_path) -> None:
    tpl = tmp_path / "tpl"
    tpl.mkdir()
    (tpl / "a.txt").write_text("x")
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "existing").write_text("keep")
    with pytest.raises(FileExistsError):
        render_tree(tpl, dest, {})
