"""System endpoints: health and (read-only, Phase 1) settings."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import StrategyConfig, deep_merge, default_strategy_config, get_settings, invalidate_config_cache
from ..database import ping
from ..models import AppSetting, TradingSession
from .deps import Authed, db_session

router = APIRouter()


STALE_AFTER_WEEKDAYS = 2   # tolerates one exchange holiday without a false alarm


@router.get("/api/health")
def health() -> dict:
    """Liveness plus the two things that silently go wrong: stale data and failing jobs.

    Unauthenticated (for uptime monitors), so it carries counts and dates only, never error text or data.
    ``status`` is "degraded" when the database is unreachable, live data is stale, or a job failed in 24 h.
    """
    s = get_settings()
    db_ok, db_detail = ping()
    latest = _latest_session() if db_ok else None
    fresh = _freshness(latest) if db_ok else None
    jobs = _jobs() if db_ok else None
    problems = [] if db_ok else ["database unreachable"]
    if db_ok and latest is None:
        problems.append("no NSE sessions loaded yet (see docs/DEPLOYMENT.md, first data load)")
    elif fresh and fresh["stale"] and s.data_mode == "live":
        problems.append(f"data stale: {fresh['missing_weekdays']} expected sessions missing")
    if fresh and s.data_mode == "live":
        behind: dict = {}
        for name, last in (fresh.get("benchmarks") or {}).items():
            if latest and (last is None or last < latest):
                behind.setdefault(last, []).append(name)
        for last, names in behind.items():
            problems.append(f"{', '.join(names)} (Yahoo) last {last or 'never'}; carried forward for {latest}")
    if jobs and jobs["failures_24h"]:
        problems.append(f"{jobs['failures_24h']} job failure(s) in 24 h")
    return {
        "status": "ok" if not problems else "degraded",
        "problems": problems,
        "version": __version__,
        "time_ist": datetime.now(ZoneInfo(s.timezone)).isoformat(timespec="seconds"),
        "environment": s.app_env,
        "data_mode": s.data_mode,
        "demo": s.data_mode == "demo",
        "execution_mode": "paper",
        "database": {"reachable": db_ok, "detail": db_detail},
        "ai_enabled": bool(s.ai_enabled and s.anthropic_api_key),
        "latest_session": latest,
        "data_freshness": fresh,
        "jobs": jobs,
    }


def _freshness(latest) -> dict | None:
    from datetime import timedelta
    from ..market_data.calendar import bhavcopy_expected, now_ist
    if latest is None:
        return {"stale": True, "missing_weekdays": None, "benchmarks": _benchmarks()}
    today = now_ist().date()
    expected = today if bhavcopy_expected(today) else today - timedelta(days=1)
    missing, d = 0, latest + timedelta(days=1)
    while d <= expected:
        missing += d.weekday() < 5
        d += timedelta(days=1)
    return {"stale": missing > STALE_AFTER_WEEKDAYS, "missing_weekdays": missing, "benchmarks": _benchmarks()}


def _benchmarks() -> dict:
    """Last stored session per benchmark. Scoring carries a missing index bar forward for up to 3 sessions
    (scoring/panel.py), so a benchmark behind the latest NSE session means figures use a stale index value."""
    from sqlalchemy import func
    from ..database import _factory
    from ..models import MarketCandle, Stock
    names = ("NIFTY 50", "NIFTY 500", "INDIA VIX")
    try:
        with _factory()() as db:
            rows = dict(db.execute(select(Stock.symbol, func.max(MarketCandle.session_date)).join(MarketCandle, MarketCandle.stock_id == Stock.id)
                                   .where(Stock.kind == "index", Stock.symbol.in_(names)).group_by(Stock.symbol)).all())
    except Exception:  # noqa: BLE001
        return {}
    return {n: rows.get(n) for n in names}


def _jobs() -> dict | None:
    from datetime import timedelta, timezone
    from sqlalchemy import func
    from ..database import _factory
    from ..models import JobRun
    try:
        with _factory()() as db:
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            last = dict(db.execute(select(JobRun.job, func.max(JobRun.finished_at)).where(JobRun.status == "success")
                                   .group_by(JobRun.job)).all())
            failures = db.scalar(select(func.count()).select_from(JobRun).where(JobRun.status == "failed",
                                                                               JobRun.started_at >= since))
    except Exception:  # noqa: BLE001 - table missing before migrations
        return None
    return {"last_success": {k: v.isoformat(timespec="seconds") for k, v in last.items() if v}, "failures_24h": failures}


def _latest_session():
    from sqlalchemy import func
    from ..database import _factory
    with _factory()() as db:
        return db.scalar(select(func.max(TradingSession.session_date)).where(TradingSession.source == "nse_archive"))


@router.get("/api/v1/settings")
def read_settings() -> dict:
    """Effective strategy configuration (defaults overlaid with the latest saved dashboard settings)."""
    s = get_settings()
    cfg = default_strategy_config()
    return {
        "strategy": cfg.model_dump(mode="json"),
        "defaults": StrategyConfig().model_dump(mode="json"),
        "derived": {"risk_budget_per_position_inr": cfg.risk_budget_per_position_inr},
        "data": {"mode": s.data_mode, "daily_provider": s.daily_provider, "intraday_provider": s.intraday_provider},
        "editable": True,
        "locked": {"execution_mode": "paper (live order placement does not exist in this codebase)"},
    }


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


class SettingsIn(BaseModel):
    changes: dict = Field(description="partial configuration, nested like GET /api/v1/settings 'strategy'")
    note: str | None = Field(None, max_length=500)


@router.put("/api/v1/settings", dependencies=[Authed])
def update_settings(body: SettingsIn, db: Session = Depends(db_session)) -> dict:
    """Validate and save settings. Every save is a new audit row; nothing is applied if validation fails."""
    if "execution_mode" in body.changes and body.changes["execution_mode"] != "paper":
        raise HTTPException(422, "execution_mode can only be 'paper'")
    current = default_strategy_config()
    defaults = StrategyConfig().model_dump(mode="json")
    merged = deep_merge(current.model_dump(mode="json"), body.changes)
    try:
        new = StrategyConfig.model_validate(merged)
    except ValidationError as e:
        raise HTTPException(422, [{"field": ".".join(str(x) for x in err["loc"]), "error": err["msg"]} for err in e.errors()])
    before, after = _flatten(current.model_dump(mode="json")), _flatten(new.model_dump(mode="json"))
    changes = [{"path": k, "from": before.get(k), "to": v} for k, v in after.items() if before.get(k) != v]
    if not changes:
        return {"saved": False, "reason": "no change", "strategy": new.model_dump(mode="json")}
    flat_defaults = _flatten(defaults)
    overrides: dict = {}
    for k, v in after.items():
        if flat_defaults.get(k) != v:
            cur = overrides
            *parents, leaf = k.split(".")
            for part in parents:
                cur = cur.setdefault(part, {})
            cur[leaf] = v
    # keep whole sub-dicts whose keys are data (e.g. regime.policies, window weights) intact
    for key in ("regime", "relative_strength"):
        if key in overrides:
            overrides[key] = new.model_dump(mode="json")[key]
    row = AppSetting(key="strategy", value=overrides, changes=changes, note=body.note, updated_by="dashboard")
    db.add(row)
    db.commit()
    invalidate_config_cache()
    rehash = any(c["path"].startswith(("universe_rules", "min_median", "liquidity", "score_", "relative_strength", "strategies"))
                 for c in changes)
    return {"saved": True, "id": row.id, "changes": changes, "strategy": new.model_dump(mode="json"),
            "note": ("Applies from the next end-of-day run. " + ("Universe, scoring or strategy rules changed: a new set of "
                     "snapshots/scores/signals is built under a new rule hash; earlier results stay stored." if rehash else ""))}


@router.get("/api/v1/settings/history")
def settings_history(limit: int = 50, db: Session = Depends(db_session)) -> dict:
    rows = db.execute(select(AppSetting).where(AppSetting.key == "strategy").order_by(AppSetting.id.desc()).limit(limit)).scalars().all()
    return {"history": [{"id": r.id, "updated_at": r.updated_at, "updated_by": r.updated_by, "note": r.note, "changes": r.changes}
                        for r in rows]}


@router.post("/api/v1/settings/reset", dependencies=[Authed])
def reset_settings(db: Session = Depends(db_session)) -> dict:
    current = default_strategy_config().model_dump(mode="json")
    defaults = StrategyConfig().model_dump(mode="json")
    changes = [{"path": k, "from": v, "to": _flatten(defaults).get(k)} for k, v in _flatten(current).items() if _flatten(defaults).get(k) != v]
    db.add(AppSetting(key="strategy", value={}, changes=changes, note="reset to defaults", updated_by="dashboard"))
    db.commit()
    invalidate_config_cache()
    return {"saved": True, "changes": changes}
