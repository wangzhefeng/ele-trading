"""v5 V5-2：状态条件 t-Copula、极端模板与强制保留。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.forecasting.market_state import MarketStateForecast
from ele_trading.scenario.reduction import reduce_scenarios
from ele_trading.scenario.state_conditioned import (
    ExtremeScenarioTemplate,
    StateConditionedScenarioBuilder,
)


TZ = "Asia/Shanghai"
ISSUE_TIME = pd.Timestamp("2026-07-01 00:00", tz=TZ)
VALID_TIMES = pd.date_range(
    ISSUE_TIME + pd.Timedelta(minutes=15), periods=4, freq="15min"
)
TARGETS = ("price", "day_ahead_price", "real_time_price", "load")


def _forecast(target: str, value: float, unit: str) -> ForecastResult:
    request_target = "price" if "price" in target else target
    request = ForecastRequest(
        target=request_target,
        scope_type="market",
        scope_id="single_settlement",
        horizon=len(VALID_TIMES),
        frequency="15min",
        issue_time=ISSUE_TIME,
        quantiles=(0.1, 0.9),
        data={"price_role": "real_time_settlement"}
        if request_target == "price"
        else {},
    )
    point = pd.Series(value, index=VALID_TIMES)
    return ForecastResult(
        request=request,
        point=point,
        quantiles={0.1: point - 1.0, 0.9: point + 1.0},
        unit=unit,
        model_version=f"{target}-v1",
        feature_as_of=ISSUE_TIME,
    )


def _forecasts() -> dict[str, ForecastResult]:
    return {
        "price": _forecast("price", 200.0, "CNY/MWh"),
        "day_ahead_price": _forecast("day_ahead_price", 180.0, "CNY/MWh"),
        "real_time_price": _forecast("real_time_price", 200.0, "CNY/MWh"),
        "load": _forecast("load", 10.0, "MWh/period"),
    }


def _residual_inputs() -> tuple[pd.DataFrame, pd.Series]:
    index = pd.date_range(
        "2026-06-25 00:00", periods=48, freq="15min", tz=TZ
    )
    states = np.repeat(
        ["normal", "strained", "congested", "extreme"], 12
    )
    rng = np.random.default_rng(7)
    state_shift = {
        "normal": 0.0,
        "strained": 20.0,
        "congested": 50.0,
        "extreme": 100.0,
    }
    price_residual = np.array([state_shift[item] for item in states])
    residuals = pd.DataFrame(
        {
            "price": price_residual + rng.normal(0.0, 2.0, len(index)),
            "day_ahead_price": 0.7 * price_residual + rng.normal(0.0, 2.0, len(index)),
            "real_time_price": price_residual + rng.normal(0.0, 2.0, len(index)),
            "load": np.maximum(price_residual / 50.0, 0.0)
            + rng.normal(0.0, 0.05, len(index)),
        },
        index=index,
    )
    return residuals, pd.Series(states, index=index)


def _state_forecast(state: str = "extreme") -> MarketStateForecast:
    columns = ("normal", "strained", "congested", "extreme")
    probabilities = pd.DataFrame(0.0, index=VALID_TIMES, columns=columns)
    probabilities.loc[:, state] = 1.0
    return MarketStateForecast(
        issue_time=ISSUE_TIME,
        valid_time_index=VALID_TIMES,
        probabilities=probabilities,
        state_definition_version="state-v1",
        feature_as_of=ISSUE_TIME,
        model_version="state-model-v1",
        model_kind="physical",
        feature_version="state-features-v1",
    )


def _builder() -> StateConditionedScenarioBuilder:
    residuals, labels = _residual_inputs()
    return StateConditionedScenarioBuilder(
        residual_history=residuals,
        state_labels=labels,
        residual_as_of=residuals.index[-1],
        state_definition_version="state-v1",
        t_copula_df=4.0,
        min_state_samples=8,
    )


def test_extreme_state_changes_distribution_and_same_seed_reproduces():
    builder = _builder()
    first = builder.build(
        forecasts=_forecasts(),
        market_state_forecast=_state_forecast("extreme"),
        num_scenarios=24,
        random_seed=17,
    )
    second = builder.build(
        forecasts=_forecasts(),
        market_state_forecast=_state_forecast("extreme"),
        num_scenarios=24,
        random_seed=17,
    )

    first_prices = np.vstack(
        [item.trajectories["price"].to_numpy() for item in first.scenarios]
    )
    second_prices = np.vstack(
        [item.trajectories["price"].to_numpy() for item in second.scenarios]
    )
    assert float(first_prices.mean()) > 270.0
    assert np.array_equal(first_prices, second_prices)
    assert first.metadata["dependence_model"] == "state_conditioned_t_copula"
    assert first.metadata["t_copula_df"] == 4.0
    assert set(first.source_versions) == set(TARGETS)


def test_extreme_template_is_injected_and_forced_through_reduction():
    template = ExtremeScenarioTemplate(
        template_id="negative-price-cap-event",
        state="extreme",
        additive_shocks={
            "price": np.full(4, -800.0),
            "real_time_price": np.full(4, -800.0),
            "load": np.full(4, 5.0),
        },
        calibrated_probability=0.05,
        evidence_version="event-2025-07-09-v1",
        rule_version="rule-v3",
    )
    scenarios = _builder().build(
        forecasts=_forecasts(),
        market_state_forecast=_state_forecast(),
        num_scenarios=8,
        random_seed=23,
        extreme_templates=(template,),
    )

    forced_ids = tuple(scenarios.metadata["forced_scenario_ids"])
    assert forced_ids == ("extreme:negative-price-cap-event",)
    extreme = next(
        item for item in scenarios.scenarios if item.scenario_id == forced_ids[0]
    )
    assert float(extreme.trajectories["price"].min()) < 0.0

    reduced, diagnostics = reduce_scenarios(
        scenarios,
        top_k=2,
        preserve_critical_events=False,
        forced_scenario_ids=forced_ids,
        return_diagnostics=True,
    )
    assert forced_ids[0] in {item.scenario_id for item in reduced.scenarios}
    assert diagnostics.forced_scenarios_retained


def test_state_conditioned_builder_rejects_future_residual_vintage():
    residuals, labels = _residual_inputs()
    builder = StateConditionedScenarioBuilder(
        residual_history=residuals,
        state_labels=labels,
        residual_as_of=ISSUE_TIME + pd.Timedelta(minutes=1),
        state_definition_version="state-v1",
    )
    with pytest.raises(ValueError, match="later than issue_time"):
        builder.build(
            forecasts=_forecasts(),
            market_state_forecast=_state_forecast(),
            num_scenarios=4,
            random_seed=1,
        )


def test_template_requires_probability_provenance():
    with pytest.raises(ValueError, match="evidence_version"):
        ExtremeScenarioTemplate(
            template_id="missing-evidence",
            state="extreme",
            additive_shocks={"price": np.ones(4)},
            calibrated_probability=0.01,
            evidence_version="",
            rule_version="rule-v3",
        )
