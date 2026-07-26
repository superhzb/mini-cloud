"""``ref-showcase`` entrypoint — run the web server via uvicorn on the canonical ``PORT``."""

from __future__ import annotations

import sys

import uvicorn
from mini_cloud.config import load_settings


def main() -> int:
    settings = load_settings()
    uvicorn.run(
        "ref_showcase.app:app",
        host="127.0.0.1",
        port=settings.port or 19208,
        log_config=None,  # obs owns logging; don't let uvicorn install its own handlers
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
