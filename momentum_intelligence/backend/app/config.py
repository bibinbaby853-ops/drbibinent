"""Configuration.

Two layers:

* ``Settings``: infrastructure (database URL, data mode, secrets) read from the
  environment or a ``.env`` file. Secrets are ``SecretStr`` and are never
  serialised to the API.
* ``StrategyConfig``: the trading configuration from the project brief. It is
  validated here so an inconsistent configuration cannot start the system. In
  Phase 8 the dashboard edits it through ``PUT /api/v1/settings`` and every
  change is stored with an audit trail.

Live order placement does not exist in this codebase; ``execution_mode`` can
only be ``"paper"``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Timeframe = Literal["15m", "60m", "1d", "1w"]


class ScoreWeights(BaseModel):
    """Momentum score weights (brief section 8). Must sum to 100."""

    model_config = ConfigDict(extra="forbid")

    trend: float = 25
    relative_strength: float = 20
    volume: float = 15
    price_action: float = 15
    sector: float = 10
    momentum_indicators: float = 10
    risk_reward: float = 5

    @model_validator(mode="after")
    def _sum_to_100(self) -> "ScoreWeights":
        total = sum(self.model_dump().values())
        if abs(total - 100) > 1e-9:
            raise ValueError(f"score weights must sum to 100, got {total:g}")
        if any(v < 0 for v in self.model_dump().values()):
            raise ValueError("score weights cannot be negative")
        return self


class ScoreBands(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: float = 85
    strong: float = 75
    developing: float = 65

    @model_validator(mode="after")
    def _ordered(self) -> "ScoreBands":
        if not (100 >= self.priority > self.strong > self.developing > 0):
            raise ValueError("score bands must satisfy 100 >= priority > strong > developing > 0")
        return self


Regime = Literal["strong_bullish", "moderately_bullish", "sideways", "moderately_bearish", "strong_bearish"]
REGIMES: tuple[str, ...] = ("strong_bullish", "moderately_bullish", "sideways", "moderately_bearish", "strong_bearish")


class RegimePolicy(BaseModel):
    """What a market regime permits for NEW entries. It never closes open positions by itself."""

    model_config = ConfigDict(extra="forbid")

    allow_new_entries: bool
    max_new_entries_per_day: int = Field(ge=0)
    risk_multiplier: float = Field(ge=0, le=1)
    min_score: float = Field(ge=0, le=100)


def _default_policies() -> dict[str, RegimePolicy]:
    return {
        "strong_bullish": RegimePolicy(allow_new_entries=True, max_new_entries_per_day=3, risk_multiplier=1.0, min_score=65),
        "moderately_bullish": RegimePolicy(allow_new_entries=True, max_new_entries_per_day=2, risk_multiplier=0.75, min_score=75),
        "sideways": RegimePolicy(allow_new_entries=True, max_new_entries_per_day=1, risk_multiplier=0.5, min_score=85),
        "moderately_bearish": RegimePolicy(allow_new_entries=False, max_new_entries_per_day=0, risk_multiplier=0.0, min_score=100),
        "strong_bearish": RegimePolicy(allow_new_entries=False, max_new_entries_per_day=0, risk_multiplier=0.0, min_score=100),
    }


class RegimeConfig(BaseModel):
    """Regime classifier thresholds on the composite score (-100..+100) and the per-regime entry policy."""

    model_config = ConfigDict(extra="forbid")

    strong_bullish_min: float = 50
    moderately_bullish_min: float = 20
    sideways_min: float = -20
    moderately_bearish_min: float = -50
    policies: dict[str, RegimePolicy] = Field(default_factory=_default_policies)

    @model_validator(mode="after")
    def _check(self) -> "RegimeConfig":
        if not (100 >= self.strong_bullish_min > self.moderately_bullish_min > self.sideways_min > self.moderately_bearish_min >= -100):
            raise ValueError("regime thresholds must be strictly descending within -100..100")
        if set(self.policies) != set(REGIMES):
            raise ValueError(f"regime policies must cover exactly {REGIMES}")
        return self


class RelativeStrengthConfig(BaseModel):
    """Weights of the excess-return windows (sessions) in the combined RS rank; renormalised over available windows."""

    model_config = ConfigDict(extra="forbid")

    window_weights: dict[int, float] = Field(default_factory=lambda: {21: 0.2, 63: 0.4, 126: 0.2, 252: 0.2})
    min_window: int = 63            # a stock needs at least this window to be ranked
    leading_rank: float = 90
    improving_rank_change: float = 20
    change_lookback: int = 20       # sessions for rank change ("4 weeks")
    resilience_lookback: int = 63
    resilience_min_down_days: int = 5
    resilient_top_pct: float = Field(20, gt=0, le=100)  # "resilient" = positive AND in this top share of the universe

    @model_validator(mode="after")
    def _check(self) -> "RelativeStrengthConfig":
        if not self.window_weights or any(w < 0 for w in self.window_weights.values()) or sum(self.window_weights.values()) <= 0:
            raise ValueError("RS window weights must be non-negative with a positive sum")
        if self.min_window not in self.window_weights:
            raise ValueError("min_window must be one of the RS windows")
        return self


class UniverseConfig(BaseModel):
    """Eligibility rules for the point-in-time universe (Phase 3). Liquidity settings live on StrategyConfig."""

    model_config = ConfigDict(extra="forbid")

    # EQ = normal rolling settlement. BE/BZ are trade-to-trade / non-compliant segments, excluded by default.
    eligible_series: tuple[str, ...] = ("EQ",)
    min_history_sessions: int = Field(100, ge=20)        # enough for 50/100-day trends; 200-DMA may still be NaN
    quality_lookback_sessions: int = Field(20, ge=1)     # no blocking data issue in this window
    corporate_action_quarantine_sessions: int = Field(252, ge=0)  # ADR-011
    exclude_gsm: bool = True                             # graded surveillance (where flags are known, ADR-015)
    unclassified_sector: str = "Unclassified"            # ADR-013


class StrategyConfig(BaseModel):
    """Trading configuration (brief section 1, 8 and 12). Percentages are in percent units."""

    model_config = ConfigDict(extra="forbid")

    market: Literal["India"] = "India"
    exchange: Literal["NSE"] = "NSE"
    universe: Literal["all_eligible_nse_equities"] = "all_eligible_nse_equities"
    strategy: Literal["positional_momentum"] = "positional_momentum"
    holding_period_weeks_min: int = Field(2, ge=1)
    holding_period_weeks_max: int = Field(8, ge=1)

    simulated_capital_inr: float = Field(100_000, gt=0)
    execution_mode: Literal["paper"] = "paper"
    monitoring: Literal["intraday", "end_of_day"] = "intraday"

    primary_timeframe: Timeframe = "1d"
    trend_timeframe: Timeframe = "1w"
    confirmation_timeframe: Timeframe = "60m"
    alert_timeframe: Timeframe = "15m"

    max_positions: int = Field(5, ge=1, le=50)
    risk_per_position_pct: float = Field(0.5, gt=0, le=2)
    max_position_exposure_pct: float = Field(20, gt=0, le=100)
    max_sector_exposure_pct: float = Field(30, gt=0, le=100)
    cash_reserve_pct: float = Field(20, ge=0, lt=100)
    max_initial_aggregate_risk_pct: float = Field(2.5, gt=0, le=10)
    drawdown_alerts_pct: tuple[float, ...] = (3.0, 5.0)

    min_median_traded_value_cr: float = Field(5.0, ge=0)
    liquidity_lookback_sessions: int = Field(20, ge=5)
    universe_rules: UniverseConfig = UniverseConfig()
    relative_strength: RelativeStrengthConfig = RelativeStrengthConfig()
    regime: RegimeConfig = RegimeConfig()

    score_weights: ScoreWeights = ScoreWeights()
    score_bands: ScoreBands = ScoreBands()

    @model_validator(mode="after")
    def _consistent(self) -> "StrategyConfig":
        if self.holding_period_weeks_min > self.holding_period_weeks_max:
            raise ValueError("holding_period_weeks_min cannot exceed holding_period_weeks_max")
        if self.risk_per_position_pct > self.max_initial_aggregate_risk_pct:
            raise ValueError("risk_per_position_pct cannot exceed max_initial_aggregate_risk_pct")
        if self.max_position_exposure_pct > 100 - self.cash_reserve_pct:
            raise ValueError("max_position_exposure_pct cannot exceed the investable share (100 - cash_reserve_pct)")
        if self.max_sector_exposure_pct < self.max_position_exposure_pct:
            raise ValueError("max_sector_exposure_pct must be at least max_position_exposure_pct")
        order = {"15m": 0, "60m": 1, "1d": 2, "1w": 3}
        if not (order[self.alert_timeframe] <= order[self.confirmation_timeframe]
                <= order[self.primary_timeframe] <= order[self.trend_timeframe]):
            raise ValueError("timeframes must nest: alert <= confirmation <= primary <= trend")
        if list(self.drawdown_alerts_pct) != sorted(self.drawdown_alerts_pct) or any(x <= 0 for x in self.drawdown_alerts_pct):
            raise ValueError("drawdown_alerts_pct must be positive and ascending")
        return self

    @property
    def risk_budget_per_position_inr(self) -> float:
        return self.simulated_capital_inr * self.risk_per_position_pct / 100


class Settings(BaseSettings):
    """Infrastructure settings from environment variables (prefix ``MI_``) or ``.env``."""

    model_config = SettingsConfigDict(env_prefix="MI_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    timezone: str = "Asia/Kolkata"
    database_url: str = "postgresql+psycopg://momentum:momentum@localhost:5432/momentum"

    # Data: "demo" uses labelled synthetic data only; "live" uses the providers below.
    data_mode: Literal["demo", "live"] = "demo"
    daily_provider: Literal["nse_archive", "yahoo", "kite", "demo"] = "nse_archive"
    intraday_provider: Literal["yahoo", "kite", "demo"] = "yahoo"
    # Raw downloads are kept here (data/historical) so a re-ingest never refetches and lineage hashes can be re-verified.
    data_dir: Path = Path(__file__).resolve().parents[2] / "data"
    # Politeness towards public archives: minimum seconds between requests to one host, and retry policy.
    http_min_interval_s: float = 0.4
    http_max_retries: int = 4
    http_timeout_s: float = 30.0

    # Secrets: never returned by the API, never logged.
    api_token: SecretStr | None = None
    kite_api_key: SecretStr | None = None
    kite_api_secret: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    ai_enabled: bool = False
    ai_model: str = "claude-opus-5-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def default_strategy_config() -> StrategyConfig:
    return StrategyConfig()
