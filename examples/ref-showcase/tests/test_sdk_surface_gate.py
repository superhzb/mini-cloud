"""AST/import-resolved coverage gate for every public mini-cloud SDK export.

This is deliberately not a source-text search: comments, docstrings, and unrelated methods named
``list``/``connect``/``chat`` cannot satisfy it. A symbol counts only when source imports it from
the module that publicly exports it and then references that resolved local name.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path
from types import ModuleType

from mini_cloud.config import CANONICAL_ENV_KEYS

SRC = Path(__file__).parents[1] / "src" / "ref_showcase"

TOP_LEVEL_PACKAGES = (
    "mini_cloud.config",
    "mini_cloud.db",
    "mini_cloud.storage",
    "mini_cloud.obs",
    "mini_cloud.inference",
    "mini_cloud.analytics",
)

# Public APIs intentionally not re-exported at a package root. Keeping this explicit prevents the
# gate from silently forgetting an important submodule such as obs.asgi.
DOCUMENTED_PUBLIC_SUBMODULES: dict[str, tuple[str, ...]] = {
    "mini_cloud.obs.asgi": ("install",),
}


def _exports(module_name: str) -> set[str]:
    module = importlib.import_module(module_name)
    exported = getattr(module, "__all__", None)
    assert exported is not None, f"{module_name} must define __all__"
    return set(exported)


def _public_submodules(package_name: str) -> set[str]:
    """Find child modules that declare an explicit public surface."""
    package = importlib.import_module(package_name)
    paths = getattr(package, "__path__", ())
    found: set[str] = set()
    for info in pkgutil.iter_modules(paths, f"{package_name}."):
        module: ModuleType = importlib.import_module(info.name)
        if getattr(module, "__all__", None):
            found.add(info.name)
    return found


def _public_class_methods(module_name: str) -> set[tuple[str, str, str]]:
    """Inventory methods/properties defined by exported classes (excluding inherited internals)."""
    module = importlib.import_module(module_name)
    methods: set[tuple[str, str, str]] = set()
    for class_name in _exports(module_name):
        candidate = getattr(module, class_name)
        if not inspect.isclass(candidate):
            continue
        for method_name, value in vars(candidate).items():
            if method_name.startswith("_"):
                continue
            if callable(value) or isinstance(value, classmethod | staticmethod | property):
                methods.add((module_name, class_name, method_name))
    return methods


def _resolved_references(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    used_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    used_attributes = {
        (node.value.id, node.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Load)
        and isinstance(node.value, ast.Name)
    }

    resolved: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name != "*" and local_name in used_names:
                    resolved.add((node.module, alias.name))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", maxsplit=1)[0]
                for owner, attribute in used_attributes:
                    if owner == local_name:
                        resolved.add((alias.name, attribute))
    return resolved


def _resolved_method_references(path: Path) -> set[tuple[str, str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_classes: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_classes[alias.asname or alias.name] = (node.module, alias.name)

    resolved: set[tuple[str, str, str]] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in imported_classes
        ):
            module_name, class_name = imported_classes[node.value.id]
            resolved.add((module_name, class_name, node.attr))
    return resolved


def test_every_public_sdk_symbol_is_imported_and_referenced_from_src() -> None:
    expected = {
        (module_name, symbol)
        for module_name in TOP_LEVEL_PACKAGES
        for symbol in _exports(module_name)
    }
    expected.update(
        (module_name, symbol)
        for module_name, symbols in DOCUMENTED_PUBLIC_SUBMODULES.items()
        for symbol in symbols
    )
    covered = set().union(*(_resolved_references(path) for path in SRC.rglob("*.py")))

    missing = sorted(expected - covered)
    assert not missing, "public SDK symbols not imported and referenced:\n" + "\n".join(
        f"  {module}.{symbol}" for module, symbol in missing
    )


def test_every_exported_class_method_has_a_resolved_canary_reference() -> None:
    expected = set().union(*(_public_class_methods(package) for package in TOP_LEVEL_PACKAGES))
    covered = set().union(*(_resolved_method_references(path) for path in SRC.rglob("*.py")))
    missing = sorted(expected - covered)
    assert not missing, (
        "public SDK methods without class-resolved canary references:\n"
        + "\n".join(f"  {module}.{class_name}.{method}" for module, class_name, method in missing)
    )


def test_every_declared_public_submodule_is_explicitly_allowlisted() -> None:
    discovered = set().union(*(_public_submodules(package) for package in TOP_LEVEL_PACKAGES))
    allowlisted = set(DOCUMENTED_PUBLIC_SUBMODULES)
    unexpected = discovered - allowlisted
    assert not unexpected, (
        "new SDK public submodule(s) need an explicit coverage inventory: "
        + ", ".join(sorted(unexpected))
    )


def test_inference_project_canonical_config_is_visible_to_the_gate() -> None:
    """The newest canonical key must stay rendered by the config tour, not only SDK internals."""
    assert "MINI_INFERENCE_PROJECT" in CANONICAL_ENV_KEYS
    covered = set().union(*(_resolved_references(path) for path in SRC.rglob("*.py")))
    assert ("mini_cloud.config", "CANONICAL_ENV_KEYS") in covered

    attributes: set[str] = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        attributes.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    assert "inference_project" in attributes
