"""Named configurations for walk-forward candidates and sensitivity variants (all derived from the default)."""

from __future__ import annotations

from datetime import date

from ..config import CostConfig, ExitConfig, RegimeConfig, RegimePolicy, StrategyConfig, StrategyRules


def _with(base: StrategyConfig, **kw) -> StrategyConfig:
    return StrategyConfig.model_validate({**base.model_dump(), **kw})


def _strategies(base, enabled):
    return _with(base, strategies=StrategyRules(**{**base.strategies.model_dump(), "enabled": enabled}))


def _exits(base, **kw):
    return _with(base, exits=ExitConfig(**{**base.exits.model_dump(), **kw}))


def _regime_open(base):
    pol = {k: RegimePolicy(allow_new_entries=True, max_new_entries_per_day=3, risk_multiplier=1.0, min_score=0)
           for k in base.regime.policies}
    return _with(base, regime=RegimeConfig(**{**base.regime.model_dump(), "policies": pol}))


def _regime_strict(base):
    pols = {k: v.model_dump() for k, v in base.regime.policies.items()}
    pols["sideways"] = {"allow_new_entries": False, "max_new_entries_per_day": 0, "risk_multiplier": 0.0, "min_score": 100}
    return _with(base, regime=RegimeConfig(**{**base.regime.model_dump(), "policies": pols}))


def candidates(base: StrategyConfig) -> dict[str, StrategyConfig]:
    """Small a-priori grid for walk-forward selection (8 configurations)."""
    out = {}
    for sname, enabled in (("all", ("breakout", "continuation", "pullback")), ("no_breakout", ("continuation", "pullback"))):
        for tname, trail in (("trail3", 3.0), ("trail2", 2.0)):
            for gname, gate in (("gate", None), ("strict", _regime_strict)):
                c = _exits(_strategies(base, enabled), trail_atr=trail)
                if gate:
                    c = gate(c)
                out[f"{sname}|{tname}|{gname}"] = c
    return out


def sensitivity_variants(base: StrategyConfig) -> dict[str, StrategyConfig]:
    v = {"default": base}
    for r in (0.25, 1.0):
        v[f"risk_{r}%"] = _with(base, risk_per_position_pct=r, max_initial_aggregate_risk_pct=max(base.max_initial_aggregate_risk_pct, 5 * r))
    v["regime_gate_off"] = _regime_open(base)
    v["regime_strict"] = _regime_strict(base)
    for s in ("breakout", "pullback", "continuation"):
        v[f"only_{s}"] = _strategies(base, (s,))
    v["no_breakout"] = _strategies(base, ("continuation", "pullback"))
    for t in (2.0, 4.0):
        v[f"trail_{t}atr"] = _exits(base, trail_atr=t)
    v["no_partial_exit"] = _exits(base, partial_exit_r=None)
    for ts in (20, 60):
        v[f"time_stop_{ts}"] = _exits(base, time_stop_sessions=ts)
    v["zero_costs"] = _with(base, execution={**base.execution.model_dump(), "costs": CostConfig(
        stt_pct=0, exchange_txn_pct=0, sebi_fee_pct=0, stamp_duty_buy_pct=0, gst_pct=0, dp_charge_inr=0).model_dump(),
        "slippage": {**base.execution.slippage.model_dump(), "high_bps": 0, "medium_bps": 0, "low_bps": 0}})
    v["capital_10L"] = _with(base, simulated_capital_inr=1_000_000)
    return v


# ---------------------------------------------------------------- redesign (pre-registered before any design-period result)

HOLDOUT_START = date(2025, 7, 1)   # never used for design; evaluated once at the end (see research protocol)
DESIGN_START = date(2021, 7, 1)
DESIGN_SPLIT = date(2023, 7, 1)    # consistency check: each half must be positive


def _regime_soft(base):
    """Bearish regimes allowed at reduced size and higher score bar instead of blocked."""
    pols = {k: v.model_dump() for k, v in base.regime.policies.items()}
    pols["sideways"] = {"allow_new_entries": True, "max_new_entries_per_day": 2, "risk_multiplier": 1.0, "min_score": 65}
    pols["moderately_bearish"] = {"allow_new_entries": True, "max_new_entries_per_day": 1, "risk_multiplier": 0.5, "min_score": 75}
    pols["strong_bearish"] = {"allow_new_entries": True, "max_new_entries_per_day": 1, "risk_multiplier": 0.5, "min_score": 80}
    return _with(base, regime=RegimeConfig(**{**base.regime.model_dump(), "policies": pols}))


def _score_floor(base, floor):
    pols = {k: {**v.model_dump(), "min_score": max(v.min_score, floor)} for k, v in base.regime.policies.items()}
    return _with(base, regime=RegimeConfig(**{**base.regime.model_dump(), "policies": pols}))


def designs(base: StrategyConfig) -> dict[str, tuple[str, StrategyConfig]]:
    """Six candidate designs, each tied to a Phase 7 finding. Returns name -> (rationale, config)."""
    nb = _strategies(base, ("continuation", "pullback"))
    runners = lambda c: _exits(c, partial_exit_r=None, time_stop_sessions=60)  # noqa: E731
    d3 = runners(_regime_soft(nb))
    return {
        "D0_baseline": ("current defaults (reference only)", base),
        "D1_no_breakout_gate_off": ("Breakout lost in every variant; the regime gate blocked profitable trades", _regime_open(nb)),
        "D2_no_breakout_soft_gate": ("keep a bearish brake but trade it smaller with a higher score bar", _regime_soft(nb)),
        "D3_soft_gate_runners": ("let winners run: no partial exit, 12-week time stop (both helped in sensitivity)", d3),
        "D4_runners_score75": ("D3 plus a score floor of 75 in every regime (quality over quantity)", _score_floor(d3, 75)),
        "D5_runners_tighter_stops": ("D3 with stops capped at 2 ATR: more capital deployed within the same 0.5% risk",
                                     _with(d3, strategies=StrategyRules(**{**d3.strategies.model_dump(), "max_stop_atr": 2.0}))),
    }


def profile_config(profile: str | None, base: StrategyConfig) -> StrategyConfig:
    """Configuration for a paper account's strategy profile (forward testing). None -> the base configuration."""
    if not profile:
        return base
    d = designs(base)
    if profile not in d:
        raise ValueError(f"unknown profile {profile!r}; choose from {list(d)}")
    return d[profile][1]
