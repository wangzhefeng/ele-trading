"""Orchestrator event-chain tests (v3 M5 / D-006)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ele_trading.domain.contracts import PositionState
from ele_trading.domain.events import (
    DispatchEvent,
    ForecastEvent,
    MeteringEvent,
    PositionEvent,
    SettlementEvent,
    derive_input_versions,
)
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.operations.multi_resource import (
    BESSUnit,
    MultiResourcePortfolio,
)
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.orchestrator import TradingOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"

DECISION_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")  # type: ignore[assignment]

BESS = {
    "p_bcmax": 2.0,
    "p_bdmax": 2.0,
    "p_bceff": 0.95,
    "p_bdeff": 0.95,
    "socmin": 1.0,
    "socmax": 5.0,
    "socini": 3.0,
    "cap": 4.0,
}


class _StaticDataProvider:
    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            monthly_positions={"2026-07": 4.0},
            budget_remaining=10_000.0,
            risk_exposure=0.0,
            source_version="position-v1",
        )


class _StaticForecastProvider:
    def forecast(self, request: ForecastRequest) -> ForecastResult:
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
        point = pd.Series(values[: request.horizon], index=index)
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - 0.1, 0.9: point + 0.1},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}-v1",
            feature_as_of=request.issue_time,
        )


def _run_pipeline(*, multi_resource_portfolio: MultiResourcePortfolio | None = None):
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=_StaticDataProvider(),
        forecast_provider=_StaticForecastProvider(),
        forecast_registry="registry-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v1",
        multi_resource_portfolio=multi_resource_portfolio,
    )
    return orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
    )


def test_event_chain_complete_and_ordered():
    """事件链按日前预测/调度、日内新 vintage/调度、实际结算排列。"""
    result = _run_pipeline()
    event_types = [type(event) for event in result.events]
    assert event_types == (
        [PositionEvent]
        + [ForecastEvent] * 5
        + [DispatchEvent]
        + [ForecastEvent] * 4
        + [DispatchEvent]
        + [MeteringEvent, SettlementEvent]
    )
    assert [event.source for event in result.events] == [
        "position",
        "price:day_ahead_reference",
        "price",
        "load",
        "wind_power",
        "pv_power",
        "dispatch:day_ahead",
        "price:real_time_settlement:intraday",
        "load:intraday",
        "wind_power:intraday",
        "pv_power:intraday",
        "dispatch:intraday",
        "metering",
        "settlement:single_settlement",
    ]


def test_event_sources_and_versions_traceable():
    """每个决策可经事件链回溯来源与版本（v3 不变量 7）。"""
    result = _run_pipeline()
    by_source = {event.source: event for event in result.events}
    assert by_source["position"].version == "position-v1"
    assert by_source["price"].version == "price-v1"
    assert by_source["load"].version == "load-v1"
    assert by_source["dispatch:day_ahead"].version == (
        "single-settlement-operational-v1"
    )
    assert by_source["settlement:single_settlement"].version == "config-v1"
    assert all(event.calendar.freq_minutes == 15 for event in result.events)
    assert all(event.unit for event in result.events)


def test_input_versions_derived_from_event_chain():
    """每个 DecisionTrace 只由该决策时刻已经发生的事件派生。"""
    result = _run_pipeline()
    day_ahead_derived = derive_input_versions(
        result.events[:6],
        extra={"forecast_registry": "registry-v1"},
    )
    day_ahead_trace = result.day_ahead_plan.decision_trace
    assert day_ahead_trace is not None
    assert day_ahead_trace.input_versions == day_ahead_derived
    assert day_ahead_trace.input_versions["position"] == "position-v1"
    assert day_ahead_trace.input_versions["price"] == "price-v1"

    intraday_derived = derive_input_versions(
        result.events[:11],
        extra={"forecast_registry": "registry-v1"},
    )
    intraday_trace = result.intraday_plan.schedule.decision_trace
    assert intraday_trace is not None
    assert intraday_trace.input_versions == intraday_derived
    assert (
        intraday_trace.input_versions[
            "price:real_time_settlement:intraday"
        ]
        == "price-v1"
    )


def test_forecast_events_respect_decision_time():
    """每个预测必须先于有效时段，并使用对应的日前或日内决策时刻。"""
    result = _run_pipeline()
    intraday_decision_time = DECISION_TIME + pd.Timedelta(minutes=30)
    for event in result.events:
        if isinstance(event, ForecastEvent):
            assert event.issue_time < event.valid_time
            expected_issue_time = (
                intraday_decision_time
                if event.source.endswith(":intraday")
                else DECISION_TIME
            )
            assert event.issue_time == expected_issue_time


def test_configured_multi_resource_portfolio_returns_separate_resource_plan():
    result = _run_pipeline(
        multi_resource_portfolio=MultiResourcePortfolio(
            bess_units=(
                BESSUnit(
                    name="bess-a",
                    soc0=3.0,
                    soc_min=1.0,
                    soc_max=5.0,
                    p_charge_max=2.0,
                    p_discharge_max=2.0,
                    eta_charge=0.95,
                    eta_discharge=0.95,
                ),
            ),
        ),
    )

    resource_plan = result.multi_resource_result
    assert resource_plan is not None
    assert resource_plan.solve_result.status.value == "optimal"
    assert "bess-a" in resource_plan.resource_schedules
    trace = result.day_ahead_plan.decision_trace
    assert trace is not None
    assert trace.diagnostics["multi_resource.solve_status"] == "optimal"
