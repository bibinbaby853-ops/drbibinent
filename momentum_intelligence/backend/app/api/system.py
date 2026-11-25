"""System endpoints: health and (read-only, Phase 1) settings."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from .. import __version__
from ..config import default_strategy_config, get_settings
from ..database import ping

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    s = get_settings()
    db_ok, db_detail = ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "version": __version__,
        "time_ist": datetime.now(ZoneInfo(s.timezone)).isoformat(timespec="seconds"),
        "environment": s.app_env,
        "data_mode": s.data_mode,
        "demo": s.data_mode == "demo",
        "execution_mode": "paper",
        "database": {"reachable": db_ok, "detail": db_detail},
        "ai_enabled": bool(s.ai_enabled and s.anthropic_api_key),
    }


@router.get("/api/v1/settings")
def read_settings() -> dict:
    """Current strategy configuration. Editing arrives with the settings table in Phase 2/8."""
    s = get_settings()
    cfg = default_strategy_config()
    return {
        "strategy": cfg.model_dump(),
        "derived": {"risk_budget_per_position_inr": cfg.risk_budget_per_position_inr},
        "data": {"mode": s.data_mode, "daily_provider": s.daily_provider, "intraday_provider": s.intraday_provider},
        "editable": False,
    }
