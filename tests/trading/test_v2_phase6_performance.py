"""Phase 6 performance-budget tests (v2 §10.6).

Marked ``slow`` and skipped by default (see pyproject ``addopts``). Run explicitly:

    uv run pytest -m slow -q
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.backtest import run_walk_forward_backtest
from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.day_ahead_coupled import solve_day_ahead_operational
from ele_trading.trading.intraday_rolling import solve_intraday_rolling
from ele_trading.trading.orchestrator import TradingOrchestrator
from ele_trading.trading.sample_data import (
    SampleTradingDataProvider,
    WalkForwardSeasonalNaiveProvider,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_TRADING = PROJECT_ROOT / "data" / "trading"
MENGXI_YAML = PROJECT_ROOT / "configs" / "trading" / "market_mengxi.yaml"

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


def _ninety_six_point_day():
    """Return (net_load, price, q_long, p_long) arrays for one 96-point sample day."""
    provider = SampleTradingDataProvider(DATA_TRADING)
    day = provider.available_days[-1]
    frame = provider.frame_for_day(day)
    assert len(frame) == 96
    return (
        frame["Q_real_load"].to_numpy(dtype=float),
        frame["p_real"].to_numpy(dtype=float),
        frame["Q_long"].to_numpy(dtype=float),
        frame["p_long"].to_numpy(dtype=float),
    )


@pytest.mark.slow
def test_deterministic_day_ahead_lp_under_5_seconds():
    """96-point deterministic LP must solve within the §10.6 budget (≤5s)."""
    config = load_market_config(MENGXI_YAML)
    config.scenario_cvar_weight = 0.0
    net_load, price, q_long, p_long = _ninety_six_point_day()

    start = time.perf_counter()
    solve_day_ahead_operational(
        net_load,
        price,
        SAMPLE_BESS,
        config,
        q_long=q_long,
        p_long=p_long,
        p_ref=price,
    )
    elapsed = time.perf_counter() - start

    assert elapsed <= 5.0, f"deterministic day-ahead LP took {elapsed:.2f}s (>5s)"


@pytest.mark.slow
def test_single_intraday_rolling_under_10_seconds():
    """A single intraday rolling solve must finish within the §10.6 budget (≤10s)."""
    config = load_market_config(MENGXI_YAML)
    config.scenario_cvar_weight = 0.0
    net_load, price, q_long, p_long = _ninety_six_point_day()
    mid = len(net_load) // 2

    day_ahead = solve_day_ahead_operational(
        net_load,
        price,
        SAMPLE_BESS,
        config,
        q_long=q_long,
        p_long=p_long,
        p_ref=price,
    )
    executed_prefix = day_ahead.resource_schedule.iloc[:mid]

    start = time.perf_counter()
    solve_intraday_rolling(
        load_forecast=net_load[mid:],
        realtime_price_forecast=price[mid:],
        current_soc=float(day_ahead.soc.iloc[mid]),
        bess=SAMPLE_BESS,
        config=config,
        previous_plan=day_ahead,
        executed_prefix=executed_prefix,
        q_long=q_long[mid:],
        p_long=p_long[mid:],
        p_ref=price[mid:],
    )
    elapsed = time.perf_counter() - start

    assert elapsed <= 10.0, f"intraday rolling took {elapsed:.2f}s (>10s)"


@pytest.mark.slow
def test_thirty_day_walk_forward_under_10_minutes():
    """The full ~30-day walk-forward backtest must finish within §10.6 (≤10min)."""
    provider = SampleTradingDataProvider(DATA_TRADING)
    days = provider.available_days
    frames = {day: provider.frame_for_day(day) for day in days}
    calendar = {
        pd.Timestamp(day.date(), tz="Asia/Shanghai"): frames[day][
            ["Q_real_load", "p_real"]
        ].copy()
        for day in days[1:]
    }
    config = load_market_config(MENGXI_YAML)
    orchestrator = TradingOrchestrator(
        data_provider=provider,
        forecast_provider=WalkForwardSeasonalNaiveProvider(frames),
        forecast_registry="seasonal-naive-walkforward-v1",
        scenario_builder=build_joint_scenarios,
        config=config,
        bess=SAMPLE_BESS,
        config_version="perf-v1",
    )

    start = time.perf_counter()
    run_walk_forward_backtest(
        calendar,
        orchestrator=orchestrator,
        intraday_start=48,
        risk_aware_weight=1.0,
    )
    elapsed = time.perf_counter() - start

    assert elapsed <= 600.0, f"30-day walk-forward took {elapsed:.1f}s (>600s)"
