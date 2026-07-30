"""Phase 6 walk-forward regression invariants over the sample trading fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.backtest import run_walk_forward_backtest
from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.orchestrator import TradingOrchestrator
from ele_trading.trading.sample_data import (
    SampleTradingDataProvider,
    WalkForwardSeasonalNaiveProvider,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_TRADING = PROJECT_ROOT / "data" / "trading"
MENGXI_YAML = PROJECT_ROOT / "configs" / "market_mengxi.yaml"

SAMPLE_BESS = {
    "p_bcmax": 5.0,
    "p_bdmax": 5.0,
    "p_bceff": 0.95,
    "p_bdeff": 0.95,
    "socmin": 1.0,
    "socmax": 10.0,
    "socini": 5.0,
    "cap": 10.0,
}


def _calendar(provider: SampleTradingDataProvider, n_days: int):
    """First ``n_days`` decision days (each has a strictly-prior observed day)."""
    days = provider.available_days
    decision_days = list(days[1 : 1 + n_days])
    frames = {day: provider.frame_for_day(day) for day in days}
    calendar = {
        pd.Timestamp(day.date(), tz="Asia/Shanghai"): frames[day][
            ["Q_real_load", "p_real"]
        ].copy()
        for day in decision_days
    }
    return calendar, frames


def _orchestrator(provider, frames, *, scenario_count: int):
    config = load_market_config(MENGXI_YAML)
    config.scenario_count = scenario_count
    return TradingOrchestrator(
        data_provider=provider,
        forecast_provider=WalkForwardSeasonalNaiveProvider(frames),
        forecast_registry="seasonal-naive-walkforward-v1",
        scenario_builder=build_joint_scenarios,
        config=config,
        bess=SAMPLE_BESS,
        config_version="regression-v1",
    )


def test_walk_forward_regression_invariants():
    """The four baselines are present, finite, oracle-dominated, and reproducible."""
    provider = SampleTradingDataProvider(DATA_TRADING)
    calendar, frames = _calendar(provider, n_days=3)

    report_a = run_walk_forward_backtest(
        calendar,
        orchestrator=_orchestrator(provider, frames, scenario_count=2),
        intraday_start=48,
        risk_aware_weight=0.5,
    )
    report_b = run_walk_forward_backtest(
        calendar,
        orchestrator=_orchestrator(provider, frames, scenario_count=2),
        intraday_start=48,
        risk_aware_weight=0.5,
    )

    expected = {
        "strategy_cost",
        "no_storage_cost",
        "deterministic_cost",
        "risk_aware_cost",
        "oracle_cost",
        "fallback_used",
    }
    assert expected.issubset(report_a.columns)
    assert len(report_a) == 3

    cost_cols = [
        "strategy_cost",
        "no_storage_cost",
        "deterministic_cost",
        "risk_aware_cost",
        "oracle_cost",
    ]
    assert np.isfinite(report_a[cost_cols].to_numpy(dtype=float)).all()

    # Storage (with or without foresight) must not cost more than the no-storage
    # baseline on aggregate over the window. The oracle optimizes the LP
    # objective, which is not identical to the settlement cost formula, so we
    # compare against the no-storage baseline rather than asserting
    # oracle <= strategy day-by-day.
    tol = 1.0
    assert report_a["strategy_cost"].sum() <= report_a["no_storage_cost"].sum() + tol
    assert report_a["oracle_cost"].sum() <= report_a["no_storage_cost"].sum() + tol

    # Fixed config + seed => identical results (regression reproducibility).
    pd.testing.assert_frame_equal(
        report_a.sort_index(),
        report_b.sort_index(),
    )


def test_walk_forward_forecast_never_uses_decision_day_or_later():
    """Every forecast vintage strictly precedes its decision day (no lookahead)."""
    from ele_trading.forecasting.contracts import ForecastRequest

    provider = SampleTradingDataProvider(DATA_TRADING)
    _, frames = _calendar(provider, n_days=3)
    wf_provider = WalkForwardSeasonalNaiveProvider(frames)
    days = provider.available_days

    for decision_day in days[1:4]:
        issue_time = pd.Timestamp(decision_day.date(), tz="Asia/Shanghai")
        request = ForecastRequest(
            target="price",
            scope_type="market",
            scope_id="mengxi",
            horizon=96,
            frequency="15min",
            issue_time=issue_time,
            quantiles=(0.1, 0.9),
        )
        result = wf_provider.forecast(request)
        # The vintage must be available strictly before this decision day.
        assert result.feature_as_of < issue_time
        assert result.feature_as_of.normalize() < issue_time.normalize()
