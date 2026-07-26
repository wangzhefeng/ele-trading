"""Round-1 regressions for Phase 4 joint scenarios and optimization."""

from __future__ import annotations

import pandas as pd
import pytest


ISSUE_TIME = pd.Timestamp("2026-07-01 10:00", tz="Asia/Shanghai")
VALID_TIMES = pd.date_range(
    "2026-07-01 10:15",
    periods=3,
    freq="15min",
    tz="Asia/Shanghai",
)


def _forecast_result(
    target: str,
    point_values: list[float],
    *,
    quantile_values: dict[float, list[float]],
    unit: str,
):
    from ele_trading.forecasting.contracts import (
        ForecastRequest,
        ForecastResult,
    )

    levels = tuple(sorted(quantile_values))
    request = ForecastRequest(
        target=target,
        scope_type="site",
        scope_id="north-1",
        horizon=len(point_values),
        frequency="15min",
        issue_time=ISSUE_TIME,
        quantiles=levels,
    )
    index = VALID_TIMES[: len(point_values)]
    return ForecastResult(
        request=request,
        point=pd.Series(point_values, index=index, dtype=float),
        quantiles={
            level: pd.Series(
                quantile_values[level],
                index=index,
                dtype=float,
            )
            for level in levels
        },
        unit=unit,
        model_version=f"{target}-v1",
        feature_as_of=ISSUE_TIME - pd.Timedelta(minutes=15),
    )


def _joint_forecasts(*, price_with_distinct_median: bool = False):
    price_quantiles = {
        0.1: [270.0],
        0.9: [370.0],
    }
    if price_with_distinct_median:
        price_quantiles[0.5] = [330.0]
    return {
        "price_forecast": _forecast_result(
            "price",
            [300.0],
            quantile_values=price_quantiles,
            unit="CNY/MWh",
        ),
        "load_forecast": _forecast_result(
            "load",
            [8.0],
            quantile_values={0.1: [7.0], 0.9: [9.0]},
            unit="MW",
        ),
        "wind_forecast": _forecast_result(
            "wind_power",
            [2.0],
            quantile_values={0.1: [1.0], 0.9: [3.0]},
            unit="MW",
        ),
        "pv_forecast": _forecast_result(
            "pv_power",
            [1.0],
            quantile_values={0.1: [0.5], 0.9: [1.5]},
            unit="MW",
        ),
    }


def _scenario(
    scenario_id: str,
    probability: float,
    values: list[float],
):
    from ele_trading.scenario.contracts import Scenario

    return Scenario(
        scenario_id=scenario_id,
        probability=probability,
        issue_time=ISSUE_TIME,
        trajectories={
            "load": pd.Series(
                values,
                index=VALID_TIMES[: len(values)],
                dtype=float,
            )
        },
        seed=23,
        source_versions={"load": "load-v1"},
    )


def _scenario_set(scenarios):
    from ele_trading.scenario.contracts import ScenarioSet

    return ScenarioSet(
        horizon=scenarios[0].horizon,
        valid_time_index=scenarios[0].valid_time_index,
        units={"load": "MW"},
        scenarios=tuple(scenarios),
    )


def _optimization_scenario_set():
    from ele_trading.scenario.contracts import Scenario, ScenarioSet

    scenarios = tuple(
        Scenario(
            scenario_id=scenario_id,
            probability=0.5,
            issue_time=ISSUE_TIME,
            trajectories={
                "price": pd.Series([50.0], index=VALID_TIMES[:1]),
                "load": pd.Series([1.0], index=VALID_TIMES[:1]),
                "wind_power": pd.Series([0.0], index=VALID_TIMES[:1]),
                "pv_power": pd.Series([0.0], index=VALID_TIMES[:1]),
            },
            seed=23,
            source_versions={
                "price": "price-v1",
                "load": "load-v1",
                "wind_power": "wind-v1",
                "pv_power": "pv-v1",
            },
        )
        for scenario_id in ("base", "stress")
    )
    return ScenarioSet(
        horizon=1,
        valid_time_index=VALID_TIMES[:1],
        units={
            "price": "CNY/MWh",
            "load": "MW",
            "wind_power": "MW",
            "pv_power": "MW",
        },
        scenarios=scenarios,
    )


def test_joint_builder_preserves_forecast_median_distinct_from_point():
    """Overwriting supplied q0.5 with point destroys the forecast distribution."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    scenario_set = build_joint_scenarios(
        **_joint_forecasts(price_with_distinct_median=True),
        num_scenarios=5,
        requested_quantiles={"price": (0.5,)},
        random_seed=19,
    )

    assert [
        item.trajectories["price"].iloc[0]
        for item in scenario_set.scenarios
    ] == pytest.approx([330.0] * 5)


def test_joint_builder_uses_supplied_median_when_tail_quantiles_requested():
    """Selecting tail anchors must not replace an available q0.5 with point."""
    import numpy as np

    from ele_trading.scenario.joint_builder import build_joint_scenarios

    scenario_set = build_joint_scenarios(
        **_joint_forecasts(price_with_distinct_median=True),
        num_scenarios=2001,
        requested_quantiles={"price": (0.1, 0.9)},
        method="mc",
        random_seed=29,
    )
    prices = [
        item.trajectories["price"].iloc[0]
        for item in scenario_set.scenarios
    ]

    assert float(np.median(prices)) == pytest.approx(330.0, abs=2.0)


@pytest.mark.parametrize(
    "invalid_feature_as_of",
    [
        ISSUE_TIME + pd.Timedelta(minutes=15),
        pd.NaT,
        pd.Timestamp("2026-07-01 09:45"),
    ],
    ids=["future", "nat", "timezone-naive"],
)
def test_joint_builder_revalidates_mutated_feature_as_of(
    invalid_feature_as_of,
):
    """Post-construction mutation must not bypass forecast provenance checks."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    forecasts = _joint_forecasts()
    forecasts["load_forecast"].feature_as_of = invalid_feature_as_of

    with pytest.raises(ValueError, match="feature_as_of"):
        build_joint_scenarios(
            **forecasts,
            num_scenarios=3,
        )


def test_scenario_set_rejects_inconsistent_source_versions_at_construction():
    """Late property failure is too weak for a ScenarioSet provenance contract."""
    first = _scenario("first", 0.5, [1.0, 1.0, 1.0])
    second = _scenario("second", 0.5, [2.0, 2.0, 2.0])
    second.source_versions["load"] = "load-v2"

    with pytest.raises(ValueError, match="source_versions"):
        _scenario_set([first, second])


def test_reduction_top_k_equal_count_preserves_duplicate_scenario_weights():
    """A retained duplicate must keep its own mass instead of becoming zero."""
    from ele_trading.scenario.reduction import reduce_scenarios

    original = _scenario_set(
        [
            _scenario("duplicate-a", 0.4, [1.0, 1.0, 1.0]),
            _scenario("duplicate-b", 0.6, [1.0, 1.0, 1.0]),
        ]
    )

    reduced, diagnostics = reduce_scenarios(
        original,
        top_k=2,
        return_diagnostics=True,
    )

    assert {
        item.scenario_id: item.probability
        for item in reduced.scenarios
    } == pytest.approx({"duplicate-a": 0.4, "duplicate-b": 0.6})
    assert diagnostics.probability_transfers == {}


def test_reduction_transfers_only_removed_duplicate_probability():
    """Retained duplicate mass must not be reassigned to another retained path."""
    from ele_trading.scenario.reduction import reduce_scenarios

    original = _scenario_set(
        [
            _scenario("duplicate-a", 0.1, [0.0, 0.0, 0.0]),
            _scenario("duplicate-b", 0.2, [0.0, 0.0, 0.0]),
            _scenario("duplicate-c", 0.3, [0.0, 0.0, 0.0]),
            _scenario("peak", 0.2, [100.0, 100.0, 100.0]),
            _scenario("ramp", 0.2, [0.0, 50.0, 0.0]),
        ]
    )

    reduced, diagnostics = reduce_scenarios(
        original,
        top_k=4,
        return_diagnostics=True,
    )

    assert {
        item.scenario_id: item.probability
        for item in reduced.scenarios
    } == pytest.approx(
        {
            "duplicate-b": 0.3,
            "duplicate-c": 0.3,
            "peak": 0.2,
            "ramp": 0.2,
        }
    )
    assert diagnostics.probability_transfers == {
        "duplicate-a": "duplicate-b"
    }


def test_two_stage_reports_typed_error_for_mutated_scenario_versions():
    """An invalid post-construction ScenarioSet must not escape as an exception."""
    from ele_trading.optimization.bess_model import BESSConfig
    from ele_trading.optimization.solver import SolveStatus
    from ele_trading.optimization.two_stage_cvar import solve_two_stage_cvar

    scenario_set = _optimization_scenario_set()
    scenario_set.scenarios[1].source_versions["price"] = "price-v2"

    result = solve_two_stage_cvar(
        scenario_set,
        bess_config=BESSConfig(
            soc0=0.0,
            soc_min=0.0,
            soc_max=1.0,
            p_ch_max=1.0,
            p_dis_max=1.0,
            eta_ch=1.0,
            eta_dis=1.0,
        ),
        deviation_penalty_positive=0.25,
        deviation_penalty_negative=0.25,
    )

    assert result.solve_status is SolveStatus.ERROR
    assert result.first_stage_bid is None
    assert result.scenario_recourse == {}
    assert "source_versions" in result.solver_result.message


def test_two_stage_requires_explicit_deviation_penalties():
    """Optimization must not invent market penalty coefficients."""
    from ele_trading.optimization.bess_model import BESSConfig
    from ele_trading.optimization.two_stage_cvar import solve_two_stage_cvar

    with pytest.raises(TypeError, match="deviation_penalty"):
        solve_two_stage_cvar(
            _optimization_scenario_set(),
            bess_config=BESSConfig(
                soc0=0.0,
                soc_min=0.0,
                soc_max=1.0,
                p_ch_max=1.0,
                p_dis_max=1.0,
                eta_ch=1.0,
                eta_dis=1.0,
            ),
        )


def test_compat_builder_requires_explicit_deviation_penalties():
    """The legacy builder must not retain hidden penalty defaults."""
    from ele_trading.optimization.two_stage_cvar import (
        build_two_stage_cvar_model,
    )

    with pytest.raises(TypeError, match="kappa"):
        build_two_stage_cvar_model(
            T=[0],
            OMEGA=["base"],
            p_omega={"base": 1.0},
            pi_da={0: 50.0},
            pi_rt={(0, "base"): 50.0},
            soc0=0.0,
            soc_min=0.0,
            soc_max=1.0,
            p_ch_max=1.0,
            p_dis_max=1.0,
            eta_ch=1.0,
            eta_dis=1.0,
            deg_cost=0.0,
        )
