"""Phase 5 single-settlement trading-chain behavior."""

from __future__ import annotations
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE

from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_single_settlement_identity_when_reference_is_real_time_price():
    """Wrong contract-price reference would break the single-settlement identity."""
    from ele_trading.markets.single_settlement.settlement import (
        compute_contract_difference,
        compute_energy_cost,
    )

    q_real = np.array([8.0, 12.0])
    p_real = np.array([320.0, 410.0])
    q_long = np.array([5.0, 7.0])
    p_long = np.array([280.0, 360.0])

    actual = compute_energy_cost(q_real, p_real) + compute_contract_difference(
        q_long,
        p_long,
        p_ref=p_real,
    )
    expected = q_long * p_long + (q_real - q_long) * p_real

    np.testing.assert_allclose(actual, expected)


def test_active_contracts_describe_operations_and_itemized_settlement():
    """Reintroducing bid quantities or a bundled settlement total would break v2."""
    from dataclasses import fields

    from ele_trading.domain.contracts import (
        DecisionTrace,
        OperationalPlan,
    )
    from ele_trading.markets.single_settlement.contracts import SettlementReport

    trace = DecisionTrace(
        decision_time=pd.Timestamp("2026-07-01", tz="Asia/Shanghai"),
        input_versions={"load": "load-v1"},
        model_versions={"dispatch": "dispatch-v2"},
        config_version="sha256:test",
        solver_name="cbc",
        solver_version="2.10",
        solver_status="optimal",
        objective_components={"energy_cost": 120.0},
        active_constraints={"terminal_soc": (3,)},
    )
    schedule = pd.DataFrame(
        {
            "p_charge": [1.0, 0.0],
            "p_discharge": [0.0, 1.0],
            "p_net": [-1.0, 1.0],
        }
    )
    plan = OperationalPlan(
        resource_schedule=schedule,
        soc=pd.Series([5.0, 5.2, 4.9]),
        expected_cost=120.0,
        expected_risk=8.0,
        constraint_trace={"terminal_soc": (1,)},
        decision_trace=trace,
    )
    report = SettlementReport(
        energy_cost=100.0,
        contract_difference=-10.0,
        long_recovery=5.0,
        dr_adjustment=-3.0,
        degradation_cost=2.0,
        execution_adjustment=1.0,
        total_cost=95.0,
        baseline_cost=110.0,
        delta_cost=15.0,
        trace=trace,
    )

    assert list(plan.resource_schedule) == [
        "p_charge",
        "p_discharge",
        "p_net",
    ]
    assert report.total_cost == 95.0
    operational_fields = {field.name for field in fields(OperationalPlan)}
    assert operational_fields.isdisjoint(
        {"q_dayah", "bid_prices", "expected_revenue"}
    )


def test_active_package_exports_only_v2_trading_contracts():
    """Leaving new contracts unexported would keep callers on removed v1 names."""
    import ele_trading.domain as domain
    import ele_trading.markets.single_settlement as single_settlement
    import ele_trading.trading as trading

    for name in (
        "DecisionTrace",
        "PositionState",
        "MarketForecastBundle",
        "OperationalPlan",
        "IntradayPlan",
    ):
        assert hasattr(domain, name)
    assert hasattr(single_settlement, "SettlementReport")
    assert not hasattr(trading, "DayAheadPlan")


def test_market_config_is_one_to_one_and_contains_dr_rules():
    """Unknown or removed dual-settlement fields must not re-enter configuration."""
    from dataclasses import fields

    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.markets.single_settlement.contracts import MarketConfig

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    configured_fields = {field.name for field in fields(MarketConfig)}

    assert config.market.dt == 0.25
    assert config.market.settlement_mode == "single_settlement"
    assert config.market.long_recovery_lower_ratio == 0.90
    assert config.dr.dr_compensation_per_mwh > 0.0
    assert config.dr.dr_penalty_per_mwh > 0.0
    assert config.dr.dr_minimum_margin >= 0.0
    assert configured_fields.isdisjoint(
        {
            "lam_l",
            "lam_u",
            "dayahead_mode",
            "dayahead_price_reporting",
            "w_pen",
        }
    )


def test_settlement_report_items_adjustments_once():
    """Omitting or double-counting any signed adjustment would change the total."""
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.markets.single_settlement.settlement import (
        build_settlement_report,
        compute_long_recovery,
    )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    recovery = compute_long_recovery(
        q_long_month=50.0,
        p_long_month=400.0,
        q_real_month=100.0,
        p_ref_month=300.0,
        config=config,
    )
    report = build_settlement_report(
        q_real=np.array([8.0, 12.0]),
        p_real=np.array([300.0, 400.0]),
        q_long=np.array([5.0, 7.0]),
        p_long=np.array([280.0, 350.0]),
        p_ref=np.array([300.0, 400.0]),
        long_recovery=recovery,
        dr_adjustment=-20.0,
        degradation_cost=12.0,
        execution_adjustment=3.0,
        baseline_cost=10_000.0,
    )

    expected_total = (
        report.energy_cost
        + report.contract_difference
        + report.long_recovery
        + report.dr_adjustment
        + report.degradation_cost
        + report.execution_adjustment
    )
    assert recovery > 0.0
    assert report.total_cost == expected_total
    assert report.delta_cost == report.baseline_cost - report.total_cost


def test_day_ahead_plan_is_physical_and_ignores_explanatory_price_signal():
    """Using an explanatory day-ahead price as a financial input would alter this plan."""
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.operations.day_ahead_coupled import (
        solve_day_ahead_operational,
    )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    bess = {
        "p_bcmax": 2.0,
        "p_bdmax": 2.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 5.0,
        "socini": 3.0,
        "cap": 4.0,
    }
    load = np.full(4, 3.0)
    realtime_price = np.array([100.0, 100.0, 500.0, 500.0])

    low_signal = solve_day_ahead_operational(
        load,
        realtime_price,
        bess,
        config,
        explanatory_price_signal=np.zeros(4),
    )
    high_signal = solve_day_ahead_operational(
        load,
        realtime_price,
        bess,
        config,
        explanatory_price_signal=np.full(4, 10_000.0),
    )

    assert low_signal.resource_schedule["p_charge"].sum() > 0.0
    assert low_signal.resource_schedule["p_discharge"].sum() > 0.0
    assert low_signal.soc.between(bess["socmin"], bess["socmax"]).all()
    pd.testing.assert_frame_equal(
        low_signal.resource_schedule,
        high_signal.resource_schedule,
    )
    assert low_signal.expected_cost == high_signal.expected_cost


def test_day_ahead_objective_itemizes_contract_dr_and_scenario_cvar():
    """Dropping a cost component or the scenario tail risk would break the trace."""
    from ele_trading.scenario.contracts import Scenario, ScenarioSet
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.operations.day_ahead_coupled import (
        solve_day_ahead_operational,
    )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    config.scenario.scenario_cvar_weight = 0.25
    bess = {
        "p_bcmax": 1.0,
        "p_bdmax": 1.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 4.0,
        "socini": 2.0,
        "cap": 3.0,
    }
    issue = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
    index = pd.date_range(
        "2026-07-01 00:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    scenarios = tuple(
        Scenario(
            scenario_id=name,
            probability=0.5,
            issue_time=issue,
            trajectories={
                "price": pd.Series(prices, index=index),
                "load": pd.Series([2.0, 2.0], index=index),
            },
            seed=seed,
            source_versions={"price": "price-v1", "load": "load-v1"},
        )
        for name, prices, seed in (
            ("low", [100.0, 120.0], 1),
            ("high", [400.0, 800.0], 2),
        )
    )
    scenario_set = ScenarioSet(
        horizon=2,
        valid_time_index=index,
        units={"price": "CNY/MWh", "load": "MWh/period"},
        scenarios=scenarios,
    )

    plan = solve_day_ahead_operational(
        np.array([2.0, 2.0]),
        np.array([250.0, 450.0]),
        bess,
        config,
        q_long=np.array([1.0, 1.0]),
        p_long=np.array([300.0, 300.0]),
        p_ref=np.array([250.0, 450.0]),
        scenario_set=scenario_set,
        settlement=SINGLE_SETTLEMENT_MODE.settlement,
    )

    components = plan.decision_trace.objective_components
    assert components["contract_difference"] == -100.0
    assert plan.expected_risk > 0.0


def test_day_ahead_scenario_cost_uses_load_minus_wind_and_pv():
    """Joint scenarios must price net load, not gross demand alone."""
    from ele_trading.scenario.contracts import Scenario, ScenarioSet
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.operations.day_ahead_coupled import (
        solve_day_ahead_operational,
    )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    config.scenario.scenario_cvar_weight = 0.0
    bess = {
        "p_bcmax": 0.0,
        "p_bdmax": 0.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 2.0,
        "socini": 1.0,
        "cap": 1.0,
    }
    issue = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
    index = pd.date_range(
        "2026-07-01 00:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    scenario_set = ScenarioSet(
        horizon=2,
        valid_time_index=index,
        units={
            "price": "CNY/MWh",
            "load": "MWh/period",
            "wind_power": "MWh/period",
            "pv_power": "MWh/period",
        },
        scenarios=(
            Scenario(
                scenario_id="renewable",
                probability=1.0,
                issue_time=issue,
                trajectories={
                    "price": pd.Series([100.0, 100.0], index=index),
                    "load": pd.Series([2.0, 2.0], index=index),
                    "wind_power": pd.Series([1.0, 1.0], index=index),
                    "pv_power": pd.Series([0.5, 0.5], index=index),
                },
                seed=1,
                source_versions={
                    "price": "price-v1",
                    "load": "load-v1",
                    "wind_power": "wind-v1",
                    "pv_power": "pv-v1",
                },
            ),
        ),
    )

    plan = solve_day_ahead_operational(
        np.array([2.0, 2.0]),
        np.array([100.0, 100.0]),
        bess,
        config,
        scenario_set=scenario_set,
    )

    assert plan.decision_trace.objective_components["energy_cost"] == 100.0


def test_intraday_failure_freezes_prefix_and_clips_last_feasible_plan():
    """A failed solve must not rewrite history or execute an unsafe fallback."""
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.operations.day_ahead_coupled import (
        solve_day_ahead_operational,
    )
    from ele_trading.operations.intraday_rolling import (
        solve_intraday_rolling,
    )

    class FailingSolver:
        def actualSolve(self, model, **kwargs):
            raise RuntimeError("forced solver failure")

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    bess = {
        "p_bcmax": 2.0,
        "p_bdmax": 2.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 5.0,
        "socini": 3.0,
        "cap": 4.0,
    }
    previous = solve_day_ahead_operational(
        np.full(4, 3.0),
        np.array([100.0, 100.0, 500.0, 500.0]),
        bess,
        config,
    )
    executed = previous.resource_schedule.iloc[:2].copy()

    result = solve_intraday_rolling(
        load_forecast=np.array([0.1, 0.1]),
        realtime_price_forecast=np.array([700.0, 800.0]),
        current_soc=float(previous.soc.iloc[2]),
        bess=bess,
        config=config,
        previous_plan=previous,
        executed_prefix=executed,
        solver=FailingSolver(),
    )

    pd.testing.assert_frame_equal(result.executed_prefix, executed)
    assert result.fallback_used is True
    assert result.schedule.decision_trace.fallback_used is True
    assert (
        result.schedule.resource_schedule["p_discharge"]
        <= np.array([0.1, 0.1]) / config.market.dt + 1e-9
    ).all()
    assert result.schedule.soc.between(
        bess["socmin"],
        bess["socmax"],
    ).all()


def test_intraday_uses_latest_vintage_and_remaining_single_settlement_inputs():
    """Ignoring the rolling vintage, scenarios or contract inputs would break traceability."""
    from ele_trading.scenario.contracts import Scenario, ScenarioSet
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.operations.day_ahead_coupled import (
        solve_day_ahead_operational,
    )
    from ele_trading.operations.intraday_rolling import (
        solve_intraday_rolling,
    )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    config.scenario.scenario_cvar_weight = 0.1
    bess = {
        "p_bcmax": 2.0,
        "p_bdmax": 2.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 5.0,
        "socini": 3.0,
        "cap": 4.0,
    }
    previous = solve_day_ahead_operational(
        np.full(4, 3.0),
        np.array([100.0, 100.0, 500.0, 500.0]),
        bess,
        config,
    )
    executed = previous.resource_schedule.iloc[:2].copy()
    issue = pd.Timestamp("2026-07-01 12:00", tz="Asia/Shanghai")
    index = pd.date_range(
        "2026-07-01 12:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    scenarios = tuple(
        Scenario(
            scenario_id=name,
            probability=0.5,
            issue_time=issue,
            trajectories={
                "price": pd.Series(prices, index=index),
                "load": pd.Series([3.0, 3.0], index=index),
            },
            seed=seed,
            source_versions={"price": "price-v2", "load": "load-v2"},
        )
        for name, prices, seed in (
            ("low", [300.0, 350.0], 3),
            ("high", [700.0, 900.0], 4),
        )
    )
    scenario_set = ScenarioSet(
        horizon=2,
        valid_time_index=index,
        units={"price": "CNY/MWh", "load": "MWh/period"},
        scenarios=scenarios,
    )

    result = solve_intraday_rolling(
        load_forecast=np.array([3.0, 3.0]),
        realtime_price_forecast=np.array([500.0, 600.0]),
        current_soc=float(previous.soc.iloc[2]),
        bess=bess,
        config=config,
        previous_plan=previous,
        executed_prefix=executed,
        input_versions={"price": "price-v2", "load": "load-v2"},
        q_long=np.array([1.0, 1.0]),
        p_long=np.array([300.0, 300.0]),
        p_ref=np.array([500.0, 600.0]),
        scenario_set=scenario_set,
        settlement=SINGLE_SETTLEMENT_MODE.settlement,
    )

    assert result.fallback_used is False
    assert result.schedule.soc.iloc[0] == float(previous.soc.iloc[2])
    assert result.schedule.decision_trace.input_versions["price"] == "price-v2"
    assert (
        result.schedule.decision_trace.objective_components[
            "contract_difference"
        ]
        == -500.0
    )


def test_dr_participation_uses_only_market_config_economics():
    """Changing the configured default penalty must change the real decision."""
    import inspect

    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.demand_response.allocator import (
        evaluate_dr_participation,
    )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    config.dr.dr_window_start = 0
    config.dr.dr_window_end = 4
    config.dr.dr_compensation_per_mwh = 100.0
    config.dr.dr_penalty_per_mwh = 400.0
    config.dr.dr_minimum_margin = 10.0
    config.dr.dr_minimum_response_mwh = 0.5
    adjustable = np.ones(4)
    p_net_plan = np.zeros(4)
    price = np.full(4, 300.0)

    accepted = evaluate_dr_participation(
        adjustable,
        config,
        p_net_plan=p_net_plan,
        realtime_price_forecast=price,
        expected_shortfall_mwh=0.1,
    )
    config.dr.dr_penalty_per_mwh = 1000.0
    rejected = evaluate_dr_participation(
        adjustable,
        config,
        p_net_plan=p_net_plan,
        realtime_price_forecast=price,
        expected_shortfall_mwh=0.1,
    )

    assert accepted.participate is True
    assert accepted.expected_penalty == 40.0
    assert accepted.net_margin == 60.0
    assert rejected.participate is False
    assert "dr_compensation" not in inspect.signature(
        evaluate_dr_participation
    ).parameters
    assert "margin" not in inspect.signature(
        evaluate_dr_participation
    ).parameters


def test_mid_long_plan_has_no_financial_day_ahead_position():
    """Splitting uncovered energy into a day-ahead position would violate v2."""
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.positions.mid_long_planner import plan_mid_long_position

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    months = pd.period_range("2026-08", periods=3, freq="M")
    load = pd.Series([100.0, 120.0, 110.0], index=months)
    p_long = pd.Series([280.0, 290.0, 300.0], index=months)
    p_real = pd.Series([350.0, 360.0, 370.0], index=months)

    plan = plan_mid_long_position(
        load,
        p_long,
        p_real,
        budget=200_000.0,
        config=config,
    )

    assert not hasattr(plan, "alpha_dayah")
    assert plan.alpha_long + plan.alpha_real == 1.0
    assert plan.coverage == plan.alpha_long
    expected = float(
        np.sum(plan.q_long_monthly * p_long)
        + np.sum((load - plan.q_long_monthly) * p_real)
    )
    assert plan.expected_cost == expected


def test_monthly_gap_without_orderbook_returns_transparent_corridor():
    """Returning a synthetic order instead of a corridor would hide missing data."""
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.positions.monthly_trader import build_position_corridor

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    corridor = build_position_corridor(
        position_gap=-10.0,
        tolerance=2.0,
        price_band=(280.0, 320.0),
        config=config,
    )

    assert corridor.direction == "buy"
    assert corridor.qty_range == (8.0, 12.0)
    assert corridor.price_range == (280.0, 320.0)
    assert "orderbook" in corridor.reason


def test_orchestrator_runs_injected_single_settlement_chain_with_trace():
    """Skipping an injected stage or its version would make the pipeline opaque."""
    from ele_trading.forecasting.contracts import (
        ForecastRequest,
        ForecastResult,
    )
    from ele_trading.scenario.joint_builder import build_joint_scenarios
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.domain.contracts import PositionState
    from ele_trading.trading.orchestrator import TradingOrchestrator

    events: list[str] = []

    class StaticDataProvider:
        def get_position_state(self, decision_time, valid_time_index):
            events.append("position")
            return PositionState(
                as_of=decision_time,
                q_long=pd.Series(1.0, index=valid_time_index),
                p_long=pd.Series(300.0, index=valid_time_index),
                monthly_positions={"2026-07": 4.0},
                budget_remaining=10_000.0,
                risk_exposure=0.0,
                source_version="position-v1",
            )

    class StaticForecastProvider:
        def forecast(self, request: ForecastRequest) -> ForecastResult:
            events.append(f"forecast:{request.target}")
            values = {
                "price": [200.0, 250.0, 500.0, 600.0],
                "load": [3.0, 3.0, 3.0, 3.0],
                "wind_power": [0.0, 0.0, 0.0, 0.0],
                "pv_power": [0.0, 0.0, 0.0, 0.0],
            }[request.target]
            unit = (
                "CNY/MWh"
                if request.target == "price"
                else "MWh/period"
            )
            index = pd.date_range(
                request.issue_time + pd.Timedelta(minutes=15),
                periods=request.horizon,
                freq=request.frequency,
            )
            point = pd.Series(values, index=index)
            return ForecastResult(
                request=request,
                point=point,
                quantiles={
                    0.1: point - (20.0 if request.target == "price" else 0.1),
                    0.9: point + (20.0 if request.target == "price" else 0.1),
                },
                unit=unit,
                model_version=f"{request.target}-v1",
                feature_as_of=request.issue_time,
            )

    def recording_scenario_builder(*args, **kwargs):
        events.append("scenario")
        return build_joint_scenarios(*args, **kwargs)

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    config.scenario.scenario_count = 2
    bess = {
        "p_bcmax": 2.0,
        "p_bdmax": 2.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 5.0,
        "socini": 3.0,
        "cap": 4.0,
    }
    orchestrator = TradingOrchestrator(
        data_provider=StaticDataProvider(),
        forecast_provider=StaticForecastProvider(),
        forecast_registry="registry-v1",
        scenario_builder=recording_scenario_builder,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=bess,
        config_version="config-v1",
    )
    result = orchestrator.run(
        decision_time=pd.Timestamp(
            "2026-07-01 00:00",
            tz="Asia/Shanghai",
        ),
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 260.0, 510.0, 620.0]),
        intraday_start=2,
    )

    assert events[:6] == [
        "position",
        "forecast:price",
        "forecast:load",
        "forecast:wind_power",
        "forecast:pv_power",
        "scenario",
    ]
    assert result.intraday_plan.executed_prefix.equals(
        result.day_ahead_plan.resource_schedule.iloc[:2]
    )
    assert result.settlement_report.trace.config_version == "config-v1"
    assert (
        result.settlement_report.trace.input_versions["position"]
        == "position-v1"
    )


def test_walk_forward_backtest_keeps_actuals_out_of_production_forecasts():
    """Only the labeled oracle may turn future actual values into decisions."""
    from ele_trading.forecasting.contracts import (
        ForecastRequest,
        ForecastResult,
    )
    from ele_trading.scenario.joint_builder import build_joint_scenarios
    from ele_trading.backtest.backtest import run_walk_forward_backtest
    from ele_trading.markets.single_settlement.config_loader import load_market_config
    from ele_trading.domain.contracts import PositionState
    from ele_trading.trading.orchestrator import TradingOrchestrator

    forecast_requests: list[ForecastRequest] = []

    class PositionProvider:
        def get_position_state(self, decision_time, valid_time_index):
            return PositionState(
                as_of=decision_time,
                q_long=pd.Series(1.0, index=valid_time_index),
                p_long=pd.Series(300.0, index=valid_time_index),
                source_version="position-v1",
            )

    class ArchivedForecastProvider:
        def forecast(self, request: ForecastRequest) -> ForecastResult:
            forecast_requests.append(request)
            values = {
                "price": [200.0, 250.0, 500.0, 600.0],
                "load": [3.0, 3.0, 3.0, 3.0],
                "wind_power": [0.0, 0.0, 0.0, 0.0],
                "pv_power": [0.0, 0.0, 0.0, 0.0],
            }[request.target]
            index = pd.date_range(
                request.issue_time + pd.Timedelta(minutes=15),
                periods=request.horizon,
                freq=request.frequency,
            )
            point = pd.Series(values, index=index)
            spread = 20.0 if request.target == "price" else 0.1
            return ForecastResult(
                request=request,
                point=point,
                quantiles={0.1: point - spread, 0.9: point + spread},
                unit=(
                    "CNY/MWh"
                    if request.target == "price"
                    else "MWh/period"
                ),
                model_version=f"{request.target}-archive-v1",
                feature_as_of=request.issue_time,
            )

    config = load_market_config(
        PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    )
    config.scenario.scenario_count = 2
    config.scenario.scenario_cvar_weight = 0.2
    orchestrator = TradingOrchestrator(
        data_provider=PositionProvider(),
        forecast_provider=ArchivedForecastProvider(),
        forecast_registry="archive-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess={
            "p_bcmax": 2.0,
            "p_bdmax": 2.0,
            "p_bceff": 0.95,
            "p_bdeff": 0.95,
            "socmin": 1.0,
            "socmax": 5.0,
            "socini": 3.0,
            "cap": 4.0,
        },
        config_version="config-v1",
    )
    decision_time = pd.Timestamp(
        "2026-07-01 00:00",
        tz="Asia/Shanghai",
    )
    daily_actuals = pd.DataFrame(
        {
            "Q_real_load": [3.0, 3.0, 3.0, 3.0],
            "p_real": [210.0, 260.0, 510.0, 620.0],
        }
    )

    report = run_walk_forward_backtest(
        {decision_time: daily_actuals},
        orchestrator=orchestrator,
        intraday_start=2,
        risk_aware_weight=0.5,
    )

    assert {
        "strategy_cost",
        "no_storage_cost",
        "deterministic_cost",
        "risk_aware_cost",
        "oracle_cost",
    }.issubset(report.columns)
    assert all(request.data == {} for request in forecast_requests)
    assert all(
        request.issue_time <= decision_time
        for request in forecast_requests
    )
    assert len(forecast_requests) == 12


def test_active_tree_has_no_dual_settlement_symbols_or_todo_imports():
    """Moving only definitions while active callers retain v1 names is incomplete."""
    trading_root = (
        PROJECT_ROOT / "src" / "ele_trading" / "trading"
    )
    active_files = [
        *trading_root.glob("*.py"),
        *(PROJECT_ROOT / "app" / "trading").glob("*.py"),
    ]
    # 双结算公式符号的唯一权威实现是 markets/dual_settlement 插件；
    # 编排层（trading/ 与 app/trading/）不得重新实现或内嵌这些公式。
    prohibited = (
        "compute_settlement_C",
        "compute_settlement_C2",
        "compute_cpen_dayah",
        "DayAheadPlan",
        "q_dayah",
        "Q_dayah",
        "cpen_dayah",
        "alpha_dayah",
    )
    violations = {
        str(path.relative_to(PROJECT_ROOT)): symbol
        for path in active_files
        for symbol in prohibited
        if symbol in path.read_text()
    }
    assert violations == {}

    normal_test_imports = [
        path
        for path in (PROJECT_ROOT / "tests").rglob("*.py")
        if path != Path(__file__)
        if "ele_trading.trading.todo" in path.read_text()
    ]
    assert normal_test_imports == []

    # v1 双结算归档已删除：结算引擎激活为 markets/dual_settlement 插件，
    # 其余（v1 契约/报量报价日前/回测）由 git 历史保留。
    assert not (trading_root / "todo").exists()
    plugin = (
        PROJECT_ROOT
        / "src"
        / "ele_trading"
        / "markets"
        / "dual_settlement"
        / "settlement.py"
    ).read_text()
    assert "compute_settlement_C2" in plugin
    assert "compute_cpen_dayah" in plugin


def test_thin_pipeline_app_runs_the_injected_chain():
    """A missing or stale app entrypoint would leave the new chain unreachable."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "app" / "trading" / "run_pipeline.py"),
            "--scenario-count",
            "2",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "single-settlement pipeline" in (
        result.stdout + result.stderr
    )
