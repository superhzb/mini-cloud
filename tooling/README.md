# Shared base tooling config

One canonical ruff / pyright / pytest configuration, referenced instead of copied. This retires the
~25 verbatim copies of the same lint config scattered across the workspace's `pyproject.toml` files.

## Files

| File | Tool | How a project consumes it |
|---|---|---|
| `ruff-base.toml` | ruff | `[tool.ruff] extend = "…/tooling/ruff-base.toml"` |
| `pyright-base.json` | pyright | `pyrightconfig.json` → `{ "extends": "…/tooling/pyright-base.json", "include": ["src"] }` |
| `pytest-base.toml` | pytest | copy the `[tool.pytest.ini_options]` block, or document per project (pytest has no `extend`) |

**Pyright is strict, with a deliberate exception.** `pyright-base.json` runs `typeCheckingMode:
strict` but disables the `reportUnknown*` family, `reportMissingTypeStubs`, and `reportDeprecated`.
The platform is built on partially-typed C-extension clients (psycopg, boto3, openai) whose untyped
returns would otherwise drown the findings that matter. Real type mismatches, missing returns, and
optional-access errors are still caught — this keeps a freshly scaffolded app green on `make check`
across pyright versions without hand-annotating third-party return values.

Relative paths are project-dependent. From a sibling repo one level deep (e.g.
`mlx-platform/gateway/`) the path is `../../mini-cloud/tooling/ruff-base.toml`.

## Adopting in an existing project

Replace the copied `[tool.ruff]` / `[tool.ruff.lint]` blocks in `pyproject.toml` with:

```toml
[tool.ruff]
extend = "../../mini-cloud/tooling/ruff-base.toml"
# project-specific overrides only, e.g.:
# [tool.ruff.lint.per-file-ignores]
# "migrations/*" = ["E501"]
```

For pyright, delete the copied `[tool.pyright]` block from `pyproject.toml` and add a
`pyrightconfig.json` that `extends` the base (pyright reads `pyrightconfig.json` in preference to
`pyproject.toml`).

## First consumer

The `scaffolder/` package in this repo consumes `ruff-base.toml` and `pyright-base.json` (see its
`pyproject.toml` / `pyrightconfig.json`), satisfying the Phase 0 "consumed by at least one project"
criterion. The scaffolder later emits the same references into every project it generates, so all new
apps inherit this config for free.

## Why not a published package?

At this scale a file reference is simpler than a PyPI package and needs no install step, while still
giving one edit-point. If tooling config ever needs its own release cadence it becomes a package like
any other SDK module — boundaries + semver, per the architecture doc's decoupling model.
