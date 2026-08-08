"""V6-0 合同语义主链准入反例。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ele_trading.domain.contracts import ContractType, PositionState
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.orchestrator import TradingOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
DECISION_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
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


class _PhysicalPositionProvider:
    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="physical-position-v1",
            contract_type=ContractType.PHYSICAL_DELIVERY,
        )


class _ForecastProvider:
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        value = 300.0 if request.target == "price" else 0.0
        if request.target == "load":
            value = 3.0
        point = pd.Series(value, index=index)
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - 0.1, 0.9: point + 0.1},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}-v1",
            feature_as_of=request.issue_time,
        )


def test_orchestrator_rejects_physical_contract_without_profile_projection():
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=_PhysicalPositionProvider(),
        forecast_provider=_ForecastProvider(),
        forecast_registry="contract-admission-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v6-0",
    )

    with pytest.raises(ValueError, match="physical_delivery requires"):
        orchestrator.run(
            decision_time=DECISION_TIME,
            actual_load=np.full(4, 3.0),
            actual_price=np.full(4, 300.0),
            intraday_start=2,
        )
