import pytest
from pydantic import ValidationError

from app.config import ScoreBands, ScoreWeights, Settings, StrategyConfig


def test_defaults_match_the_project_brief():
    c = StrategyConfig()
    assert (c.market, c.exchange, c.strategy) == ("India", "NSE", "positional_momentum")
    assert (c.holding_period_weeks_min, c.holding_period_weeks_max) == (2, 8)
    assert c.simulated_capital_inr == 100_000
    assert c.execution_mode == "paper"
    assert (c.primary_timeframe, c.trend_timeframe, c.confirmation_timeframe, c.alert_timeframe) == ("1d", "1w", "60m", "15m")
    assert c.max_positions == 5
    assert c.risk_per_position_pct == 0.5
    assert c.max_position_exposure_pct == 20
    assert c.max_sector_exposure_pct == 30
    assert c.cash_reserve_pct == 20
    assert c.max_initial_aggregate_risk_pct == 2.5
    assert c.drawdown_alerts_pct == (3.0, 5.0)
    assert c.min_median_traded_value_cr == 5.0
    assert c.risk_budget_per_position_inr == 500.0


def test_score_weights_default_to_brief_and_sum_to_100():
    w = ScoreWeights()
    assert w.model_dump() == {"trend": 25, "relative_strength": 20, "volume": 15, "price_action": 15,
                              "sector": 10, "momentum_indicators": 10, "risk_reward": 5}
    with pytest.raises(ValidationError, match="sum to 100"):
        ScoreWeights(trend=30)


def test_score_bands_must_be_ordered():
    assert ScoreBands().model_dump() == {"priority": 85, "strong": 75, "developing": 65}
    with pytest.raises(ValidationError):
        ScoreBands(priority=70, strong=75)


@pytest.mark.parametrize("mode", ["live", "real", "LIVE"])
def test_live_execution_is_impossible(mode):
    with pytest.raises(ValidationError):
        StrategyConfig(execution_mode=mode)


@pytest.mark.parametrize("kwargs, message", [
    ({"risk_per_position_pct": 3}, "less than or equal to 2"),
    ({"risk_per_position_pct": 1.0, "max_initial_aggregate_risk_pct": 0.8}, "cannot exceed max_initial_aggregate_risk_pct"),
    ({"max_position_exposure_pct": 90}, "investable share"),
    ({"max_sector_exposure_pct": 10}, "at least max_position_exposure_pct"),
    ({"holding_period_weeks_min": 9}, "cannot exceed holding_period_weeks_max"),
    ({"alert_timeframe": "1d", "confirmation_timeframe": "60m"}, "timeframes must nest"),
    ({"drawdown_alerts_pct": (5.0, 3.0)}, "ascending"),
    ({"cash_reserve_pct": 100}, "less than 100"),
])
def test_inconsistent_configurations_are_rejected(kwargs, message):
    with pytest.raises(ValidationError, match=message):
        StrategyConfig(**kwargs)


def test_unknown_settings_keys_are_rejected():
    with pytest.raises(ValidationError):
        StrategyConfig(max_postions=5)  # typo must not be silently ignored


def test_secrets_are_not_exposed_in_dumps(monkeypatch):
    monkeypatch.setenv("MI_ANTHROPIC_API_KEY", "sk-test-secret")
    s = Settings()
    assert "sk-test-secret" not in repr(s)
    assert "sk-test-secret" not in str(s.model_dump())
    assert s.anthropic_api_key.get_secret_value() == "sk-test-secret"
