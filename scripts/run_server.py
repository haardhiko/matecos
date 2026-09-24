"""
run_server.py
=============
Script to run the MATECOS FastAPI application using uvicorn.
"""

from __future__ import annotations

import uvicorn

from src.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.app_debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
