"""Provision a database + bucket for a new app.

Reuses the infra stack's own tested ``scripts/create-project.sh`` rather than re-implementing
Postgres/MinIO admin — it creates a **per-project least-privilege role** (owns only its own db) and
a per-project bucket, which is exactly the credential model Phase 4 requires (the app never gets
the admin creds used to provision it). Provisioning is best-effort: if the infra stack isn't
running (no docker), the app is still scaffolded and the user is told to run the one command later.
"""

from __future__ import annotations

import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    ok: bool
    db_password: str
    detail: str


def provision_db_and_bucket(
    infra_dir: Path, name: str, *, timeout: float = 120.0
) -> ProvisionResult:
    """Create the app's DB role+database and bucket via ``infra/scripts/create-project.sh``.

    Generates a fresh random DB password (the app's least-privilege login). Returns ``ok=False``
    with a human-readable reason (rather than raising) when infra is unreachable, so ``mini new``
    degrades gracefully.
    """
    script = infra_dir / "scripts" / "create-project.sh"
    db_password = secrets.token_urlsafe(18)
    if not script.is_file():
        return ProvisionResult(False, db_password, f"provision script not found: {script}")
    try:
        proc = subprocess.run(
            [str(script), name, db_password],
            cwd=str(infra_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProvisionResult(False, db_password, f"provision failed to run: {exc}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        detail = "provision script failed (is the infra stack up?): " + " | ".join(tail)
        return ProvisionResult(False, db_password, detail)
    return ProvisionResult(True, db_password, "database + bucket created")
