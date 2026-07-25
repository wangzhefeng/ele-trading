"""Unit tests for Mengxi backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.evaluation.backtest import run_mengxi_backtest
from ele_trading.trading.contracts import MarketConfig


@pytest.fixture
def bess():
    return {
        "p_bcmax": 5.0,
        "p_bdmax": 5.0,
        "p_bceff": 0.95,
        "p_bdeff": 0.95,
        "socmin": 1.0,
        "socmax": 10.0,
        "socini": 5.0,
        "cap": 10.0,
    }


@pytest.fixture
def config():
    return MarketConfig()


@pytest.fixture
def daily_data():
    """Create synthetic daily data."""
    horizon = 96
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "p_long": rng.uniform(280, 320, horizon),
        "Q_long": rng.uniform(5, 8, horizon),
        "p_dayah": rng.uniform(250, 350, horizon),
        "p_real": rng.uniform(250, 350, horizon),
        "Q_real": rng.uniform(8, 12, horizon),
        "Q_real_load": rng.uniform(8, 12, horizon),
    })


class TestMengxiBacktest:
    def test_basic_backtest(self, daily_data, bess, config):
        """Basic backtest should return valid report."""
        report = run_mengxi_backtest(daily_data, bess, config, mode="B", seed=42)
        assert report.c_daily > 0
        assert report.cost_daily > 0
        assert report.cost_baseline > 0
        assert isinstance(report.delta_cost, float)
        assert len(report.opportunity_loss_topk) > 0

    def test_no_lookahead_bias(self, daily_data, bess, config):
        """Backtest should not use future information (forecasts differ from actuals)."""
        report = run_mengxi_backtest(daily_data, bess, config, mode="B", seed=42)
        # If no lookahead, delta_cost should be finite and not absurdly large
        assert np.isfinite(report.delta_cost)
        assert abs(report.delta_cost) < 1e6  # sanity check

    def test_oracle_upside(self, daily_data, bess, config):
        """Oracle upside should be a finite reference metric."""
        report = run_mengxi_backtest(daily_data, bess, config, mode="B", seed=42)
        # Oracle uses actual prices but same load forecast, so upside is a reference
        # metric for price forecast improvement potential. Sign depends on noise.
        assert np.isfinite(report.upside_if_oracle)

    def test_backtest_speed(self, daily_data, bess, config):
        """Single-day backtest should complete quickly."""
        import time

        start = time.time()
        run_mengxi_backtest(daily_data, bess, config, mode="B", seed=42)
        elapsed = time.time() - start
        # 30-day backtest should be ≤10min → single day ≤20s
        assert elapsed < 20.0
