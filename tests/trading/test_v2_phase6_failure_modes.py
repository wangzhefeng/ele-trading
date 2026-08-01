"""Phase 6 chain-level failure modes: data/forecast/solve failures must be observable.

These complement the Phase 5 intraday-fallback and no-lookahead tests by asserting
that forecast and data failures surface through the orchestrator/backtest rather
than being silently swallowed into a zero or garbage result (v2 §9).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.backtest.backtest import run_walk_forward_backtest
from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.domain.contracts import PositionState
from ele_trading.trading.orchestrator import TradingOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"

DECISION_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")


class _FixedPositionProvider:
    """Minimal position provider for failure-mode harnessing."""

    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="position-v1",
        )


def _result_for(request: ForecastRequest, *, issue_time=None) -> ForecastResult:
    """A well-formed forecast result, optionally with an overridden issue_time."""
    eff_request = request if issue_time is None else ForecastRequest(
        target=request.target,
        scope_type=request.scope_type,
        scope_id=request.scope_id,
        horizon=request.horizon,
        frequency=request.frequency,
        issue_time=issue_time,
        quantiles=request.quantiles,
    )
    index = pd.date_range(
        eff_request.issue_time + pd.Timedelta(minutes=15),
        periods=eff_request.horizon,
        freq=eff_request.frequency,
    )
    point = pd.Series(300.0, index=index)
    return ForecastResult(
        request=eff_request,
        point=point,
        quantiles={0.1: point - 10.0, 0.9: point + 10.0},
        unit="CNY/MWh" if request.target == "price" else "MWh/period",
        model_version=f"{request.target}-v1",
        feature_as_of=eff_request.issue_time,
    )


def _orchestrator(forecast_provider) -> TradingOrchestrator:
    config = load_market_config(MARKET_CONFIG_YAML)
    config.scenario_count = 2
    return TradingOrchestrator(
        data_provider=_FixedPositionProvider(),
        forecast_provider=forecast_provider,
        forecast_registry="failure-mode-v1",
        scenario_builder=build_joint_scenarios,
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


def _run(orchestrator: TradingOrchestrator, horizon: int = 4) -> None:
    actuals = pd.DataFrame(
        {
            "Q_real_load": [3.0] * horizon,
            "p_real": [300.0] * horizon,
        }
    )
    orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=actuals["Q_real_load"].to_numpy(dtype=float),
        actual_price=actuals["p_real"].to_numpy(dtype=float),
        intraday_start=horizon // 2,
    )


def test_orchestrator_propagates_forecast_provider_exception():
    """A failing forecast provider must surface, not silently produce a zero plan."""

    class FailingProvider:
        def forecast(self, request: ForecastRequest) -> ForecastResult:
            raise RuntimeError("forecast service unavailable")

    with pytest.raises(RuntimeError, match="forecast service unavailable"):
        _run(_orchestrator(FailingProvider()))


def test_orchestrator_rejects_forecast_with_future_issue_time():
    """A forecast whose issue_time is after the decision time must be rejected."""

    class FutureLeakingProvider:
        def forecast(self, request: ForecastRequest) -> ForecastResult:
            # Return a result stamped an hour into the future.
            return _result_for(request, issue_time=request.issue_time + pd.Timedelta(hours=1))

    with pytest.raises(ValueError, match="future information"):
        _run(_orchestrator(FutureLeakingProvider()))


def test_backtest_rejects_empty_calendar():
    """An empty backtest calendar must fail explicitly, not return an empty report."""
    config = load_market_config(MARKET_CONFIG_YAML)
    config.scenario_count = 2
    orchestrator = _orchestrator(_AlwaysThreeHundredProvider())

    with pytest.raises(ValueError, match="calendar_data must not be empty"):
        run_walk_forward_backtest(
            {},
            orchestrator=orchestrator,
            intraday_start=2,
        )


def test_backtest_rejects_daily_actuals_missing_required_columns():
    """Daily actuals lacking Q_real_load/p_real must fail, not silently infer them."""
    config = load_market_config(MARKET_CONFIG_YAML)
    config.scenario_count = 2
    orchestrator = _orchestrator(_AlwaysThreeHundredProvider())
    bad_actuals = pd.DataFrame({"load": [3.0, 3.0, 3.0, 3.0]})

    with pytest.raises(ValueError, match="daily actuals must contain columns"):
        run_walk_forward_backtest(
            {DECISION_TIME: bad_actuals},
            orchestrator=orchestrator,
            intraday_start=2,
        )


class _AlwaysThreeHundredProvider:
    """Well-formed provider so backtests reach the actuals-validation guard."""

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        return _result_for(request)
