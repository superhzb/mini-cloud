"""Template rendering: copy a template tree, substituting ``{{var}}`` in both file contents and
path components.

Deliberately tiny (no Jinja): the templates are our own, the variable set is fixed, and single
braces (FastAPI path params like ``/{id}``) must pass through untouched — only doubled braces are
substituted. A ``{{var}}`` with no matching value is an error, so a typo fails loudly instead of
emitting a literal placeholder into a generated repo.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Files copied verbatim (never text-substituted) — binaries and lockfiles.
_BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"})
_SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", ".pytest_cache", ".ruff_cache", ".venv"})


def substitute(text: str, variables: dict[str, str]) -> str:
    """Replace every ``{{var}}`` in ``text``. Raises ``KeyError`` for an unknown variable."""

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in variables:
            raise KeyError(f"template variable {{{{{key}}}}} has no value")
        return variables[key]

    return _VAR_RE.sub(repl, text)


def render_tree(template_dir: Path, dest_dir: Path, variables: dict[str, str]) -> list[Path]:
    """Render ``template_dir`` into ``dest_dir`` with ``variables``. Returns written file paths.

    Path components are substituted too, so a template file at ``src/{{package}}/app.py`` lands at
    ``src/<package>/app.py``. Refuses to overwrite a non-empty destination.
    """
    template_dir = Path(template_dir)
    dest_dir = Path(dest_dir)
    if not template_dir.is_dir():
        raise FileNotFoundError(f"template not found: {template_dir}")
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise FileExistsError(f"destination is not empty: {dest_dir}")

    written: list[Path] = []
    for src in sorted(template_dir.rglob("*")):
        if any(part in _SKIP_NAMES for part in src.relative_to(template_dir).parts):
            continue
        rel = Path(*[substitute(part, variables) for part in src.relative_to(template_dir).parts])
        # A template filename ending .tmpl drops the suffix (lets us ship e.g. `gitignore.tmpl`).
        if rel.suffix == ".tmpl":
            rel = rel.with_suffix("")
        target = dest_dir / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in _BINARY_SUFFIXES:
            shutil.copyfile(src, target)
        else:
            target.write_text(substitute(src.read_text("utf-8"), variables), encoding="utf-8")
        written.append(target)
    return written
