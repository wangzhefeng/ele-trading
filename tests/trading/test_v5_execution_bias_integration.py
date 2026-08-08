"""V5-9 执行偏差到日内滚动的集成测试。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pandas.testing as pdt

from ele_trading.domain.contracts import PositionState
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.operations.execution_bias import ExecutionBiasEstimator
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.orchestrator import TradingOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
DECISION_TIME = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai"),
)
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


class _DataProvider:
    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="position-v1",
        )


class _ForecastProvider:
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        values = {
            "price": [200.0, 250.0, 500.0, 600.0],
            "load": [3.0, 3.0, 3.0, 3.0],
            "wind_power": [0.0, 0.0, 0.0, 0.0],
            "pv_power": [0.0, 0.0, 0.0, 0.0],
        }[request.target][: request.horizon]
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        point = pd.Series(values, index=index)
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - 0.1, 0.9: point + 0.1},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}-v1",
            feature_as_of=request.issue_time,
        )


def _run(estimator: ExecutionBiasEstimator | None = None):
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=_DataProvider(),
        forecast_provider=_ForecastProvider(),
        forecast_registry="registry-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v1",
        execution_bias_estimator=estimator,
    )
    return orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
    )


def test_unavailable_execution_bias_preserves_intraday_schedule_and_records_status():
    baseline = _run()
    estimator = ExecutionBiasEstimator(window=8, min_samples=4)

    result = _run(estimator)

    pdt.assert_frame_equal(
        result.intraday_plan.schedule.resource_schedule,
        baseline.intraday_plan.schedule.resource_schedule,
    )
    trace = result.intraday_plan.schedule.decision_trace
    assert trace is not None
    assert trace.diagnostics["execution_bias.available"] == "false"
    assert trace.diagnostics["execution_bias.sample_count"] == "0"
    assert estimator.constraint_tightening().sample_count == 0


def test_persistent_shortfall_tightens_intraday_power_limit():
    estimator = ExecutionBiasEstimator(
        window=8,
        min_samples=2,
        tightening_sigma=0.0,
    )
    for _ in range(2):
        estimator.record_power(planned_mw=2.0, actual_mw=1.5)

    result = _run(estimator)

    assert result.intraday_plan.schedule.resource_schedule["p_discharge"].max() <= 1.5 + 1e-9
    trace = result.intraday_plan.schedule.decision_trace
    assert trace is not None
    assert trace.diagnostics["execution_bias.available"] == "true"
    assert trace.diagnostics["execution_bias.power_derate_mw"] == "0.5"


def test_positive_execution_bias_never_expands_physical_limits():
    estimator = ExecutionBiasEstimator(
        window=8,
        min_samples=2,
        tightening_sigma=0.0,
    )
    for _ in range(2):
        estimator.record_power(planned_mw=1.5, actual_mw=2.0)

    result = _run(estimator)

    assert result.intraday_plan.schedule.resource_schedule["p_discharge"].max() <= BESS["p_bdmax"]
    trace = result.intraday_plan.schedule.decision_trace
    assert trace is not None
    assert trace.diagnostics["execution_bias.power_derate_mw"] == "0"
