"""v5 V5-1：日前/实时价格角色与日内新 vintage 主链。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ele_trading.domain.contracts import PositionState
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.forecasting.market_state import (
    LogisticMarketStateProvider,
    MarketStateFeatureSnapshot,
    MarketStateForecast,
)
from ele_trading.markets.price_roles import PriceRole
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.scenario.state_conditioned import StateConditionedScenarioBuilder
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


class _DataProvider:
    def __init__(self) -> None:
        self.state_feature_issue_times: list[pd.Timestamp] = []

    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="position-v1",
        )

    def get_market_state_features(self, decision_time, valid_time_index):
        self.state_feature_issue_times.append(decision_time)
        return MarketStateFeatureSnapshot(
            as_of=decision_time,
            version=f"state-features:{decision_time.isoformat()}",
            frame=pd.DataFrame(
                {
                    "reserve_margin": np.linspace(0.25, 0.10, len(valid_time_index)),
                    "congestion_ratio": np.linspace(0.10, 0.80, len(valid_time_index)),
                },
                index=valid_time_index,
            ),
        )


class _RecordingForecastProvider:
    def __init__(self) -> None:
        self.requests: list[ForecastRequest] = []

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        self.requests.append(request)
        price_role = request.data.get("price_role")
        if request.target == "price":
            if price_role == PriceRole.DAY_AHEAD_REFERENCE.value:
                base = 100.0
            else:
                base = 200.0 + float(request.issue_time.hour)
        elif request.target == "load":
            base = 3.0
        else:
            base = 0.0
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        point = pd.Series(base, index=index)
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - 0.1, 0.9: point + 0.1},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}:{price_role or 'default'}:{request.issue_time.hour}",
            feature_as_of=request.issue_time,
        )


def test_orchestrator_uses_distinct_price_roles_and_intraday_vintage():
    provider = _RecordingForecastProvider()
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=_DataProvider(),
        forecast_provider=provider,
        forecast_registry="registry-v5",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v5",
    )

    result = orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
    )

    assert set(result.forecasts.price_forecasts) == {
        PriceRole.DAY_AHEAD_REFERENCE.value,
        PriceRole.REAL_TIME_SETTLEMENT.value,
    }
    assert result.forecasts.price_forecast.point.tolist() == [200.0] * 4
    assert result.intraday_forecasts is not None
    assert result.intraday_forecasts.issue_time == (
        DECISION_TIME + pd.Timedelta(minutes=30)
    )
    assert result.intraday_forecasts.price_forecast.point.tolist() == [200.0] * 2

    price_requests = [item for item in provider.requests if item.target == "price"]
    assert [item.data["price_role"] for item in price_requests] == [
        PriceRole.DAY_AHEAD_REFERENCE.value,
        PriceRole.REAL_TIME_SETTLEMENT.value,
        PriceRole.REAL_TIME_SETTLEMENT.value,
    ]
    assert [item.issue_time for item in price_requests] == [
        DECISION_TIME,
        DECISION_TIME,
        DECISION_TIME + pd.Timedelta(minutes=30),
    ]
    assert {event.source for event in result.events} >= {
        "price",
        "price:day_ahead_reference",
        "price:real_time_settlement:intraday",
        "load:intraday",
    }


def test_orchestrator_refreshes_market_state_for_each_decision_vintage():
    training_index = pd.date_range(
        "2026-06-29 00:00",
        periods=8,
        freq="15min",
        tz="Asia/Shanghai",
    )
    state_provider = LogisticMarketStateProvider(
        pd.DataFrame(
            {
                "reserve_margin": [0.4, 0.35, 0.3, 0.2, 0.15, 0.1, 0.08, 0.05],
                "congestion_ratio": [0.1, 0.2, 0.3, 0.5, 0.6, 0.75, 0.85, 0.95],
            },
            index=training_index,
        ),
        pd.Series(
            [
                "normal",
                "normal",
                "strained",
                "strained",
                "congested",
                "congested",
                "extreme",
                "extreme",
            ],
            index=training_index,
        ),
        feature_as_of=training_index[-1],
        state_definition_version="state-v1",
        model_kind="physical",
    )
    data_provider = _DataProvider()
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=data_provider,
        forecast_provider=_RecordingForecastProvider(),
        forecast_registry="registry-v5",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v5",
        market_state_provider=state_provider,
    )

    result = orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
    )

    assert result.forecasts.market_state_forecast is not None
    assert result.intraday_forecasts is not None
    assert result.intraday_forecasts.market_state_forecast is not None
    assert data_provider.state_feature_issue_times == [
        DECISION_TIME,
        DECISION_TIME + pd.Timedelta(minutes=30),
    ]
    assert {event.source for event in result.events} >= {
        "market_state",
        "market_state:intraday",
    }


class _ExtremeStateProvider:
    def forecast(self, issue_time, snapshot):
        probabilities = pd.DataFrame(
            0.0,
            index=snapshot.frame.index,
            columns=("normal", "strained", "congested", "extreme"),
        )
        probabilities.loc[:, "extreme"] = 1.0
        return MarketStateForecast(
            issue_time=issue_time,
            valid_time_index=pd.DatetimeIndex(snapshot.frame.index),
            probabilities=probabilities,
            state_definition_version="state-v1",
            feature_as_of=snapshot.as_of,
            model_version="state-extreme-v1",
            model_kind="physical",
            feature_version=snapshot.version,
        )


def test_orchestrator_routes_full_bundle_to_state_conditioned_builder():
    residual_index = pd.date_range(
        "2026-06-29 00:00",
        periods=12,
        freq="15min",
        tz="Asia/Shanghai",
    )
    residuals = pd.DataFrame(
        {
            "price": np.linspace(40.0, 60.0, 12),
            "load": np.linspace(0.5, 1.0, 12),
            "wind_power": np.zeros(12),
            "pv_power": np.zeros(12),
            "day_ahead_price": np.linspace(20.0, 30.0, 12),
            "real_time_price": np.linspace(40.0, 60.0, 12),
        },
        index=residual_index,
    )
    scenario_builder = StateConditionedScenarioBuilder(
        residual_history=residuals,
        state_labels=pd.Series("extreme", index=residual_index),
        residual_as_of=residual_index[-1],
        state_definition_version="state-v1",
        min_state_samples=4,
    )
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 3
    orchestrator = TradingOrchestrator(
        data_provider=_DataProvider(),
        forecast_provider=_RecordingForecastProvider(),
        forecast_registry="registry-v5",
        scenario_builder=scenario_builder,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v5",
        market_state_provider=_ExtremeStateProvider(),
    )

    result = orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
    )

    assert result.scenarios.metadata["dependence_model"] == (
        "state_conditioned_t_copula"
    )
    assert "day_ahead_price" in result.scenarios.units
    assert "real_time_price" in result.scenarios.units
    assert result.intraday_scenarios is not None
    assert result.intraday_scenarios.issue_time == (
        DECISION_TIME + pd.Timedelta(minutes=30)
    )
