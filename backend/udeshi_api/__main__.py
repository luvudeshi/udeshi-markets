"""``python -m udeshi_api`` runs the server (host/port from UDESHI_HOST / UDESHI_PORT)."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "udeshi_api.main:create_app",
        factory=True,
        host=os.environ.get("UDESHI_HOST", "127.0.0.1"),
        port=int(os.environ.get("UDESHI_PORT", "8000")),
        log_level=os.environ.get("UDESHI_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
