"""``python -m mini_cloud_identity`` / ``mini-cloud-identity`` — run the service under uvicorn."""

from __future__ import annotations

import uvicorn

from .config import IdentitySettings


def main() -> None:
    cfg = IdentitySettings.from_env()
    uvicorn.run(
        "mini_cloud_identity.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 — a platform service binds all interfaces on the trusted LAN
        port=cfg.port,
        log_config=None,  # obs owns logging (JSON); don't let uvicorn install its own
    )


if __name__ == "__main__":
    main()
