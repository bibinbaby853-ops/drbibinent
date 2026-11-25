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
