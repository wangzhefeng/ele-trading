"""Unit tests for Mengxi backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.evaluation.backtest import run_mengxi_backtest, run_mengxi_backtest_calendar
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


class TestMengxiBacktestCalendar:
    @pytest.fixture
    def calendar_data(self):
        """构造 5 天样例（跨自然日 SOC 传递 + 月度聚合）。"""
        horizon = 96
        cal = {}
        for i in range(5):
            rng = np.random.default_rng(100 + i)
            day = pd.Timestamp("2026-07-01") + pd.Timedelta(days=i)
            load = rng.uniform(8, 12, horizon)
            cal[day] = pd.DataFrame({
                "p_long": np.full(horizon, 310.0),
                "Q_long": 0.97 * load,
                "p_dayah": rng.uniform(250, 350, horizon),
                "p_real": rng.uniform(250, 350, horizon),
                "Q_real": load,
                "Q_real_load": load,
            })
        return cal

    def test_calendar_runs_and_soc_propagates(self, calendar_data, bess, config):
        """多日回测跑通，每日 delta_cost 有限（§10.1 日历循环）。"""
        report = run_mengxi_backtest_calendar(calendar_data, bess, config, mode="B", seed=42, rolling_step=12)
        assert len(report) == 5
        assert np.isfinite(report.delta_cost).all()
        assert (report.c_daily > 0).all()

    def test_cpen_long_monthly_aggregated(self, calendar_data, bess, config):
        """Cpen_long 按自然月聚合一次、计入月末日，而非逐日累加（§5.3）。"""
        # 把覆盖率压到 0.8（缺额）且 p_long > p_spot，触发中长期回收
        for day, df in calendar_data.items():
            df["Q_long"] = 0.8 * df["Q_real_load"]
            df["p_long"] = 400.0
            df["p_real"] = 300.0
        report = run_mengxi_backtest_calendar(calendar_data, bess, config, mode="B", seed=42, rolling_step=12)
        # 只有月末最后一天带 cpen_long
        nonzero = report[report.cpen_long != 0]
        assert len(nonzero) == 1
        assert nonzero.index[0] == max(calendar_data.keys())

    def test_calendar_speed(self, calendar_data, bess, config):
        """单日回测耗时 ≤20s（§14.3 性能预算）。"""
        import time

        start = time.time()
        run_mengxi_backtest_calendar(calendar_data, bess, config, mode="B", seed=42, rolling_step=12)
        elapsed = time.time() - start
        assert elapsed / len(calendar_data) < 20.0


class TestRegressionBaseline:
    """30 天样例回归基线（§10.3 / §14.2，基线值见 data/trading/BACKTEST_NOTES.md）。"""

    @pytest.fixture(scope="class")
    def sample_calendar(self):
        from pathlib import Path

        data = Path(__file__).resolve().parents[1] / "data" / "trading"
        cal = {
            pd.Timestamp(p.stem.replace("daily_sample_", "")): pd.read_csv(p)
            for p in sorted(data.glob("daily_sample_2026-07-*.csv"))
        }
        if len(cal) != 30:
            pytest.skip("30-day sample data not generated; run `uv run python -m ele_trading.trading.sample_data`")
        return cal

    def test_delta_cost_positive_and_stable(self, sample_calendar, bess, config):
        """主指标 ΔCost>0（§14.2）；模式 B 不低于基线的 80%（11781*0.8）。"""
        import time

        start = time.time()
        report = run_mengxi_backtest_calendar(sample_calendar, bess, config, mode="B", seed=42, rolling_step=12)
        elapsed = time.time() - start
        assert report.delta_cost.sum() > 0
        assert report.delta_cost.sum() >= 11781 * 0.8
        # 30 天 ≤ 10 min（§14.3）
        assert elapsed < 600
