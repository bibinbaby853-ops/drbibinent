"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload   (from the backend/ directory)
API docs:     http://localhost:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from . import __version__, security
from .api.system import router as system_router
from .api.backtests import router as backtests_router
from .api.dashboard import router as dashboard_router
from .api.monitoring import router as monitoring_router
from .api.portfolio import router as portfolio_router
from .api.scoring import router as scoring_router
from .api.signals import router as signals_router
from .api.universe import router as universe_router
from .config import default_strategy_config, get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    security.enforce(settings)  # production refuses to start with an open API, default password or demo data
    default_strategy_config()  # fail fast on an invalid configuration
    prod = settings.app_env == "production"
    app = FastAPI(
        title="Momentum Intelligence",
        version=__version__,
        description="Positional momentum research and PAPER portfolio management for NSE equities. No live order placement.",
        # Interactive docs and the schema are unauthenticated, so they are off in production.
        docs_url=None if prod else "/docs", redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
    )
    security.install(app)
    app.include_router(system_router)
    app.include_router(universe_router)
    app.include_router(scoring_router)
    app.include_router(portfolio_router)
    app.include_router(signals_router)
    app.include_router(backtests_router)
    app.include_router(dashboard_router)
    app.include_router(monitoring_router)
    _serve_dashboard(app)
    return app


def _serve_dashboard(app: FastAPI) -> None:
    """Serve the built dashboard (frontend/dist) from the same origin, with SPA fallback for client-side routes."""
    from pathlib import Path

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist = Path(get_settings().frontend_dist)
    if not (dist / "index.html").exists():
        return  # API-only (dashboard not built); `npm run build` in frontend/
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "not found")
        f = dist / path
        return FileResponse(f if path and f.is_file() and dist in f.resolve().parents else dist / "index.html")


app = create_app()
