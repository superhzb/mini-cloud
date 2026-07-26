"""`{{name}}` entrypoint — run the web server via uvicorn on the canonical PORT."""

from __future__ import annotations

import sys

import uvicorn
from mini_cloud.config import load_settings


def main() -> int:
    settings = load_settings()
    uvicorn.run(
        "{{package}}.app:app",
        host="127.0.0.1",
        port=settings.port or {{api_port}},
        log_config=None,  # obs owns logging
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
