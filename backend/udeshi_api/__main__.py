"""``python -m udeshi_api`` runs the server.

Host from UDESHI_HOST (default 127.0.0.1); port from UDESHI_PORT, else PORT (the convention of
many hosting platforms and dev tools), else 8000.
"""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "udeshi_api.main:create_app",
        factory=True,
        host=os.environ.get("UDESHI_HOST", "127.0.0.1"),
        port=int(os.environ.get("UDESHI_PORT") or os.environ.get("PORT") or "8000"),
        log_level=os.environ.get("UDESHI_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
