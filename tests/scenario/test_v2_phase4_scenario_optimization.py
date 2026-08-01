"""Phase 4 joint scenarios, reduction, BESS and CVaR behavior."""

from __future__ import annotations

import subprocess
import sys

import numpy as np
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
    lower_offset: float = 1.0,
    upper_offset: float = 1.0,
    unit: str = "MW",
    issue_time: pd.Timestamp = ISSUE_TIME,
    valid_times: pd.DatetimeIndex = VALID_TIMES,
    model_version: str | None = None,
):
    from ele_trading.forecasting.contracts import (
        ForecastRequest,
        ForecastResult,
    )

    request = ForecastRequest(
        target=target,
        scope_type="site",
        scope_id="north-1",
        horizon=len(point_values),
        frequency="15min",
        issue_time=issue_time,
        quantiles=(0.1, 0.9),
    )
    point = pd.Series(point_values, index=valid_times, dtype=float)
    return ForecastResult(
        request=request,
        point=point,
        quantiles={
            0.1: point - lower_offset,
            0.9: point + upper_offset,
        },
        unit=unit,
        model_version=model_version or f"{target}-v1",
        feature_as_of=issue_time - pd.Timedelta(minutes=15),
    )


def _joint_forecasts(*, horizon: int = 3):
    values = {
        "price": [300.0, 320.0, 340.0][:horizon],
        "load": [8.0, 9.0, 10.0][:horizon],
        "wind_power": [2.0, 2.5, 3.0][:horizon],
        "pv_power": [1.0, 1.5, 2.0][:horizon],
    }
    index = VALID_TIMES[:horizon]
    return {
        "price_forecast": _forecast_result(
            "price",
            values["price"],
            lower_offset=30.0,
            upper_offset=30.0,
            unit="CNY/MWh",
            valid_times=index,
        ),
        "load_forecast": _forecast_result(
            "load",
            values["load"],
            lower_offset=2.0,
            upper_offset=2.0,
            valid_times=index,
        ),
        "wind_forecast": _forecast_result(
            "wind_power",
            values["wind_power"],
            lower_offset=0.8,
            upper_offset=0.8,
            valid_times=index,
        ),
        "pv_forecast": _forecast_result(
            "pv_power",
            values["pv_power"],
            lower_offset=0.5,
            upper_offset=0.5,
            valid_times=index,
        ),
    }


def _scenario(
    scenario_id: str,
    probability: float,
    trajectories: dict[str, list[float]],
):
    from ele_trading.scenario.contracts import Scenario

    return Scenario(
        scenario_id=scenario_id,
        probability=probability,
        issue_time=ISSUE_TIME,
        trajectories={
            target: pd.Series(values, index=VALID_TIMES[: len(values)])
            for target, values in trajectories.items()
        },
        seed=17,
        source_versions={
            target: f"{target}-v1"
            for target in trajectories
        },
    )


def _scenario_set(scenarios):
    from ele_trading.scenario.contracts import ScenarioSet

    first = scenarios[0]
    targets = tuple(first.trajectories)
    return ScenarioSet(
        horizon=len(next(iter(first.trajectories.values()))),
        valid_time_index=next(iter(first.trajectories.values())).index,
        units={
            target: "CNY/MWh" if target == "price" else "MW"
            for target in targets
        },
        scenarios=tuple(scenarios),
        metadata={"fixture": "phase4"},
    )


def test_scenario_contract_rejects_negative_physical_target():
    """Removing physical-target non-negativity must fail this contract."""
    with pytest.raises(ValueError, match="non-negative"):
        _scenario(
            "negative-load",
            1.0,
            {"load": [2.0, -0.1, 3.0]},
        )


def test_scenario_set_rejects_duplicate_ids_and_probability_drift():
    """Dropping ID/probability validation must accept these invalid sets."""
    first = _scenario("same", 0.6, {"price": [1.0, 2.0, 3.0]})
    second = _scenario("same", 0.6, {"price": [2.0, 3.0, 4.0]})

    with pytest.raises(ValueError, match="unique"):
        _scenario_set([first, second])

    second = _scenario("other", 0.6, {"price": [2.0, 3.0, 4.0]})
    with pytest.raises(ValueError, match="sum to 1"):
        _scenario_set([first, second])


def test_scenario_set_rejects_misaligned_index_and_issue_time():
    """Allowing mixed valid-time grids or vintages must fail this set."""
    first = _scenario("first", 0.5, {"price": [1.0, 2.0, 3.0]})
    second = _scenario("second", 0.5, {"price": [2.0, 3.0, 4.0]})
    second.trajectories["price"].index = pd.date_range(
        "2026-07-01 10:30",
        periods=3,
        freq="15min",
        tz="Asia/Shanghai",
    )
    with pytest.raises(ValueError, match="valid-time index"):
        _scenario_set([first, second])

    second = _scenario("second", 0.5, {"price": [2.0, 3.0, 4.0]})
    second.issue_time = ISSUE_TIME - pd.Timedelta(minutes=15)
    with pytest.raises(ValueError, match="issue_time"):
        _scenario_set([first, second])


def test_joint_builder_defaults_to_lhs_and_is_reproducible():
    """Changing the default sampler or seed handling must change this output."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    forecasts = _joint_forecasts()
    first = build_joint_scenarios(
        **forecasts,
        num_scenarios=12,
        random_seed=123,
    )
    second = build_joint_scenarios(
        **forecasts,
        num_scenarios=12,
        random_seed=123,
    )

    assert first.metadata["sampling_method"] == "lhs"
    assert first.metadata["random_seed"] == 123
    assert first.source_versions == {
        "price": "price-v1",
        "load": "load-v1",
        "wind_power": "wind_power-v1",
        "pv_power": "pv_power-v1",
    }
    for left, right in zip(first.scenarios, second.scenarios):
        assert left.scenario_id == right.scenario_id
        for target in first.units:
            assert left.trajectories[target].equals(
                right.trajectories[target]
            )


def test_joint_builder_preserves_mc_compatibility():
    """Removing explicit MC support must fail this sampling contract."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    scenarios = build_joint_scenarios(
        **_joint_forecasts(),
        num_scenarios=7,
        random_seed=9,
        method="mc",
    )

    assert scenarios.metadata["sampling_method"] == "mc"
    assert len(scenarios.scenarios) == 7
    assert sum(item.probability for item in scenarios.scenarios) == pytest.approx(
        1.0
    )


def test_joint_builder_preserves_requested_correlation_direction():
    """Discarding the copula correlation must erase this positive direction."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    correlation = np.eye(4)
    correlation[0, 1] = correlation[1, 0] = 0.85
    scenarios = build_joint_scenarios(
        **_joint_forecasts(horizon=1),
        num_scenarios=600,
        random_seed=77,
        correlation_matrix=correlation,
    )
    price = np.array(
        [item.trajectories["price"].iloc[0] for item in scenarios.scenarios]
    )
    load = np.array(
        [item.trajectories["load"].iloc[0] for item in scenarios.scenarios]
    )

    assert np.corrcoef(price, load)[0, 1] > 0.7
    assert np.asarray(scenarios.metadata["correlation_matrix"]) == pytest.approx(
        correlation
    )


def test_joint_builder_rejects_misaligned_sources_and_bad_correlation():
    """Silently realigning forecasts or repairing invalid correlation is unsafe."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    forecasts = _joint_forecasts()
    misaligned_load = _forecast_result(
        "load",
        [8.0, 9.0, 10.0],
    )
    shifted_index = pd.date_range(
        "2026-07-01 10:30",
        periods=3,
        freq="15min",
        tz="Asia/Shanghai",
    )
    misaligned_load.point.index = shifted_index
    for quantile in misaligned_load.quantiles.values():
        quantile.index = shifted_index
    forecasts["load_forecast"] = misaligned_load
    with pytest.raises(ValueError, match="aligned valid-time"):
        build_joint_scenarios(**forecasts, num_scenarios=3)

    bad_correlation = np.eye(4)
    bad_correlation[0, 1] = 0.8
    with pytest.raises(ValueError, match="symmetric"):
        build_joint_scenarios(
            **_joint_forecasts(),
            num_scenarios=3,
            correlation_matrix=bad_correlation,
        )


def test_joint_builder_accepts_full_cross_time_residual_correlation():
    """Reducing dependency to only four same-time targets loses serial shape."""
    from ele_trading.scenario.joint_builder import build_joint_scenarios

    dimension = 2 * 4
    correlation = np.eye(dimension)
    price_time_zero = 0
    price_time_one = 4
    correlation[price_time_zero, price_time_one] = 0.8
    correlation[price_time_one, price_time_zero] = 0.8
    scenarios = build_joint_scenarios(
        **_joint_forecasts(horizon=2),
        num_scenarios=600,
        random_seed=31,
        correlation_matrix=correlation,
    )
    prices = np.vstack(
        [
            item.trajectories["price"].to_numpy(dtype=float)
            for item in scenarios.scenarios
        ]
    )

    assert np.corrcoef(prices[:, 0], prices[:, 1])[0, 1] > 0.65
    assert scenarios.metadata["correlation_scope"] == "target_time"


def test_legacy_price_wrapper_rejects_unknown_sampling_method():
    """Treating a typo as MC breaks the compatibility method contract."""
    from ele_trading.scenario.sampler import generate_price_scenarios

    with pytest.raises(ValueError, match="method"):
        generate_price_scenarios(
            [100.0, 200.0],
            num_scenarios=3,
            method="typo",
        )


def test_active_scenario_optimization_imports_do_not_reach_trading_or_todo():
    """Active scenario/optimization imports must remain below trading/todo."""
    probe = """
import importlib
import sys

for module_name in (
    "ele_trading.scenario",
    "ele_trading.scenario.contracts",
    "ele_trading.scenario.joint_builder",
    "ele_trading.scenario.reduction",
    "ele_trading.scenario.sampler",
    "ele_trading.optimization",
    "ele_trading.optimization.bess_arbitrage",
    "ele_trading.optimization.bess_model",
    "ele_trading.optimization.contracts",
    "ele_trading.optimization.mpc_bess",
    "ele_trading.optimization.risk",
    "ele_trading.optimization.solver",
    "ele_trading.optimization.two_stage_cvar",
):
    importlib.import_module(module_name)

for module_name in sys.modules:
    if module_name == "ele_trading.trading" or module_name.startswith("ele_trading.trading."):
        raise AssertionError(module_name)
    if ".todo" in module_name:
        raise AssertionError(module_name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_backward_reduction_is_not_probability_top_k_and_transfers_mass():
    """Replacing Wasserstein reduction with probability top-K loses extremes."""
    from ele_trading.scenario.reduction import reduce_scenarios

    original = _scenario_set(
        [
            _scenario("high-prob-a", 0.45, {"load": [0.0, 0.0, 0.0]}),
            _scenario("high-prob-b", 0.35, {"load": [1.0, 1.0, 1.0]}),
            _scenario("peak", 0.10, {"load": [100.0, 100.0, 100.0]}),
            _scenario("ramp", 0.10, {"load": [0.0, 50.0, 0.0]}),
        ]
    )

    reduced, diagnostics = reduce_scenarios(
        original,
        top_k=2,
        return_diagnostics=True,
    )

    assert {item.scenario_id for item in reduced.scenarios} == {
        "peak",
        "ramp",
    }
    assert [item.scenario_id for item in original.scenarios] == [
        "high-prob-a",
        "high-prob-b",
        "peak",
        "ramp",
    ]
    assert {
        item.scenario_id: item.probability
        for item in reduced.scenarios
    } == pytest.approx({"peak": 0.10, "ramp": 0.90})
    assert diagnostics.probability_transfers == {
        "high-prob-a": "ramp",
        "high-prob-b": "ramp",
    }
    assert diagnostics.critical_peak_scenario_id == "peak"
    assert diagnostics.critical_ramp_scenario_id == "ramp"
    assert diagnostics.critical_events_retained


def test_reduction_reports_drift_and_can_enforce_threshold():
    """Dropping distribution-drift validation must accept this lossy reduction."""
    from ele_trading.scenario.reduction import reduce_scenarios

    original = _scenario_set(
        [
            _scenario("base", 0.60, {"load": [1.0, 1.0, 1.0]}),
            _scenario("middle", 0.20, {"load": [2.0, 3.0, 2.0]}),
            _scenario("peak", 0.10, {"load": [9.0, 9.0, 9.0]}),
            _scenario("ramp", 0.10, {"load": [0.0, 8.0, 0.0]}),
        ]
    )

    reduced, diagnostics = reduce_scenarios(
        original,
        top_k=2,
        return_diagnostics=True,
    )

    assert diagnostics.wasserstein_l1 > 0.0
    assert diagnostics.mean_drift["load"] > 0.0
    assert diagnostics.quantile_drift["load"] > 0.0
    assert reduced.metadata["reduction"]["retained_count"] == 2
    with pytest.raises(ValueError, match="mean drift"):
        reduce_scenarios(
            original,
            top_k=2,
            max_mean_drift=0.01,
        )


def test_reduction_rejects_impossible_critical_event_retention():
    """Silently dropping a distinct peak or ramp violates critical retention."""
    from ele_trading.scenario.reduction import reduce_scenarios

    original = _scenario_set(
        [
            _scenario("base", 0.6, {"load": [1.0, 1.0, 1.0]}),
            _scenario("peak", 0.2, {"load": [9.0, 9.0, 9.0]}),
            _scenario("ramp", 0.2, {"load": [0.0, 8.0, 0.0]}),
        ]
    )

    with pytest.raises(ValueError, match="critical peak/ramp"):
        reduce_scenarios(original, top_k=1)


def test_bess_kernel_enforces_efficiency_terminal_throughput_and_no_export():
    """Removing any shared physical constraint changes this hand-solved path."""
    from pulp import LpMaximize, LpProblem, lpSum, value

    from ele_trading.optimization.bess_model import (
        BESSConfig,
        add_bess_constraints,
    )
    from ele_trading.optimization.solver import (
        SolveStatus,
        solve_pulp_model,
    )

    model = LpProblem("bess-physical-contract", LpMaximize)
    bess = add_bess_constraints(
        model,
        (0, 1),
        BESSConfig(
            soc0=1.0,
            soc_min=0.0,
            soc_max=2.0,
            p_ch_max=1.0,
            p_dis_max=1.0,
            eta_ch=0.5,
            eta_dis=0.5,
            dt=1.0,
            terminal_soc=0.5,
            max_throughput=1.5,
            no_export=True,
        ),
        net_load={0: 0.0, 1: 0.5},
        prefix="physical",
    )
    model += bess.p_charge[0] == 1.0
    model += lpSum(bess.p_discharge.values())

    solved = solve_pulp_model(model)

    assert solved.status is SolveStatus.OPTIMAL
    assert value(bess.p_discharge[1]) == pytest.approx(0.5)
    assert value(bess.soc[0]) == pytest.approx(1.5)
    assert value(bess.soc[1]) == pytest.approx(0.5)
    assert sum(
        value(bess.p_charge[t]) + value(bess.p_discharge[t])
        for t in (0, 1)
    ) == pytest.approx(1.5)
    assert all(
        value(bess.p_discharge[t]) - value(bess.p_charge[t])
        <= {0: 0.0, 1: 0.5}[t] + 1e-8
        for t in (0, 1)
    )


def test_cvar_auxiliary_matches_hand_computed_weighted_tail():
    """Wrong alpha scaling or probability weights must miss CVaR=5."""
    from pulp import LpMinimize, LpProblem, value

    from ele_trading.optimization.risk import (
        add_cvar_auxiliaries,
        weighted_var_cvar,
    )
    from ele_trading.optimization.solver import (
        SolveStatus,
        solve_pulp_model,
    )

    model = LpProblem("hand-cvar", LpMinimize)
    cvar = add_cvar_auxiliaries(
        model,
        losses={"base": 0.0, "stress": 10.0},
        probabilities={"base": 0.75, "stress": 0.25},
        alpha=0.5,
    )
    model += cvar.expression

    solved = solve_pulp_model(model)

    assert solved.status is SolveStatus.OPTIMAL
    assert value(cvar.var) == pytest.approx(0.0)
    assert value(cvar.expression) == pytest.approx(5.0)
    assert weighted_var_cvar(
        {"base": 0.0, "stress": 10.0},
        {"base": 0.75, "stress": 0.25},
        alpha=0.5,
    ) == pytest.approx((0.0, 5.0))


def test_solver_boundary_reports_infeasible_without_objective():
    """Treating an infeasible model as solved must fail this typed boundary."""
    from pulp import LpMinimize, LpProblem, LpVariable

    from ele_trading.optimization.solver import (
        SolveStatus,
        solve_pulp_model,
    )

    model = LpProblem("infeasible-boundary", LpMinimize)
    decision = LpVariable("decision")
    model += decision
    model += decision >= 1.0
    model += decision <= 0.0

    result = solve_pulp_model(model)

    assert result.status is SolveStatus.INFEASIBLE
    assert result.objective_value is None


def test_two_stage_optimizer_returns_typed_physical_recourse_and_cvar():
    """A model-only skeleton or untyped dict cannot satisfy this solve contract."""
    from ele_trading.optimization.bess_model import BESSConfig
    from ele_trading.optimization.solver import SolveStatus
    from ele_trading.optimization.two_stage_cvar import (
        TwoStageCVaRResult,
        solve_two_stage_cvar,
    )

    scenario_set = _scenario_set(
        [
            _scenario(
                "low",
                0.5,
                {
                    "price": [10.0, 100.0],
                    "load": [0.0, 0.0],
                    "wind_power": [0.0, 0.0],
                    "pv_power": [0.0, 0.0],
                },
            ),
            _scenario(
                "high",
                0.5,
                {
                    "price": [20.0, 120.0],
                    "load": [0.0, 0.0],
                    "wind_power": [0.0, 0.0],
                    "pv_power": [0.0, 0.0],
                },
            ),
        ]
    )
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
            dt=0.25,
            terminal_soc=0.0,
        ),
        alpha=0.5,
        risk_weight=0.2,
        degradation_cost=0.0,
        deviation_penalty_positive=0.25,
        deviation_penalty_negative=0.25,
    )

    assert isinstance(result, TwoStageCVaRResult)
    assert result.solve_status is SolveStatus.OPTIMAL
    assert result.first_stage_bid is not None
    assert len(result.first_stage_bid) == 2
    assert set(result.scenario_recourse) == {"low", "high"}
    assert result.expected_cost == pytest.approx(
        sum(
            recourse.probability * recourse.cost
            for recourse in result.scenario_recourse.values()
        )
    )
    assert result.var is not None
    assert result.cvar is not None
    assert result.cvar >= result.var - 1e-8
    assert result.trace_metadata["scenario_source_versions"] == (
        scenario_set.source_versions
    )
    for recourse in result.scenario_recourse.values():
        assert recourse.soc[-1] == pytest.approx(0.0)
        assert all(
            charge * discharge == pytest.approx(0.0, abs=1e-8)
            for charge, discharge in zip(
                recourse.p_charge,
                recourse.p_discharge,
            )
        )


def test_two_stage_optimizer_returns_failure_without_zero_schedule():
    """An infeasible no-export case must not return a fake zero schedule."""
    from ele_trading.optimization.bess_model import BESSConfig
    from ele_trading.optimization.solver import SolveStatus
    from ele_trading.optimization.two_stage_cvar import solve_two_stage_cvar

    scenario_set = _scenario_set(
        [
            _scenario(
                "surplus",
                1.0,
                {
                    "price": [50.0],
                    "load": [0.0],
                    "wind_power": [2.0],
                    "pv_power": [0.0],
                },
            )
        ]
    )
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
            dt=0.25,
            no_export=True,
        ),
        deviation_penalty_positive=0.25,
        deviation_penalty_negative=0.25,
    )

    assert result.solve_status is SolveStatus.INFEASIBLE
    assert result.first_stage_bid is None
    assert result.scenario_recourse == {}
    assert result.expected_cost is None
