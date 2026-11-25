"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload   (from the backend/ directory)
API docs:     http://localhost:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from . import __version__
from .api.system import router as system_router
from .api.scoring import router as scoring_router
from .api.universe import router as universe_router
from .config import default_strategy_config, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    default_strategy_config()  # fail fast on an invalid configuration
    app = FastAPI(
        title="Momentum Intelligence",
        version=__version__,
        description="Positional momentum research and PAPER portfolio management for NSE equities. No live order placement.",
    )
    app.include_router(system_router)
    app.include_router(universe_router)
    app.include_router(scoring_router)
    return app


app = create_app()
