"""Unit tests for intraday rolling optimization."""

from __future__ import annotations

import time

import numpy as np
import pytest

from ele_trading.trading.contracts import MarketConfig
from ele_trading.trading.intraday_rolling import solve_intraday_rolling


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


class TestIntradayRolling:
    def test_basic_solve(self, bess, config):
        """Basic intraday solve should return valid plan."""
        horizon = 48  # remaining window
        q_load = np.full(horizon, 10.0)
        p_real = np.concatenate([np.full(24, 200.0), np.full(24, 400.0)])
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        soc_current = 5.0

        plan = solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, soc_current, bess, config)
        assert plan.schedule.p_bc.shape == (horizon,)
        assert plan.schedule.p_bd.shape == (horizon,)
        assert plan.schedule.soc.shape == (horizon + 1,)
        assert plan.schedule.soc[0] == soc_current

    def test_terminal_soc_constraint(self, bess, config):
        """Terminal SOC should be respected."""
        horizon = 48
        q_load = np.full(horizon, 10.0)
        p_real = np.full(horizon, 400.0)  # high price → discharge incentive
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        soc_current = 5.0

        config.soc_terminal_min = 4.0
        plan = solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, soc_current, bess, config)
        assert plan.schedule.soc[-1] >= 4.0 - 1e-6

    def test_deviation_penalty_effect(self, bess, config):
        """High penalty weight should reduce deviation."""
        horizon = 48
        q_load = np.full(horizon, 10.0)
        p_real = np.full(horizon, 350.0)  # real > dayah
        q_dayah = np.full(horizon, 12.0)  # over-declared
        p_dayah = np.full(horizon, 300.0)
        soc_current = 5.0

        config.w_pen = 10.0  # high penalty weight
        plan = solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, soc_current, bess, config)
        # With high penalty, storage should act to reduce deviation
        assert plan.schedule.p_bc.sum() + plan.schedule.p_bd.sum() > 0

    def test_smoothness_penalty(self, bess, config):
        """Smoothness penalty should reduce plan changes."""
        horizon = 48
        q_load = np.full(horizon, 10.0)
        p_real = np.full(horizon, 300.0)
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        soc_current = 5.0
        prev_p_b = np.zeros(horizon)

        plan = solve_intraday_rolling(
            q_load, p_real, q_dayah, p_dayah, soc_current, bess, config, prev_p_b=prev_p_b
        )
        # With no price signal, should stay close to previous (zero) plan
        assert np.abs(plan.adjustment.delta_p_b).max() < 1.0

    def test_rolling_speed(self, bess, config):
        """Single rolling window should solve in ≤10s."""
        horizon = 48
        rng = np.random.default_rng(42)
        q_load = rng.uniform(5, 15, horizon)
        p_real = rng.uniform(250, 350, horizon)
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        soc_current = 5.0

        start = time.time()
        solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, soc_current, bess, config)
        elapsed = time.time() - start
        assert elapsed < 10.0

    def test_no_reverse_power(self, bess, config):
        """不可倒送：净负荷非负（§14.1）。"""
        horizon = 48
        q_load = np.full(horizon, 1.0)
        p_real = np.full(horizon, 400.0)
        q_dayah = np.full(horizon, 1.0)
        p_dayah = np.full(horizon, 300.0)

        plan = solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, 5.0, bess, config)
        net_load = q_load + (plan.schedule.p_bc - plan.schedule.p_bd) * 0.25
        assert net_load.min() >= -1e-9

    def test_nan_input_rejected(self, bess, config):
        """预测含 NaN 时报错（§11.4.2）。"""
        horizon = 48
        q_load = np.full(horizon, 10.0)
        p_real = np.full(horizon, 300.0)
        p_real[5] = np.nan
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)

        with pytest.raises(ValueError, match="NaN"):
            solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, 5.0, bess, config)

    def test_no_discharge_on_curtail(self, bess, config):
        """限电时段禁放（§2.3）。"""
        horizon = 48
        q_load = np.full(horizon, 10.0)
        p_real = np.full(horizon, 400.0)
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        t_curt = list(range(10, 20))

        config.no_discharge_on_curtail = True
        plan = solve_intraday_rolling(q_load, p_real, q_dayah, p_dayah, 5.0, bess, config, t_curt=t_curt)
        assert plan.schedule.p_bd[t_curt].max() < 1e-9

    def test_dr_lock(self, bess, config):
        """DR 履约锁定：锁定时段 p_b 等于响应曲线（§9 DR_FULFILL）。"""
        horizon = 48
        q_load = np.full(horizon, 10.0)
        p_real = np.full(horizon, 400.0)
        q_dayah = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)

        dr_lock = np.full(horizon, np.nan)
        dr_lock[10:14] = 2.0  # 锁定 4 刻以 2 MW 放电
        plan = solve_intraday_rolling(
            q_load, p_real, q_dayah, p_dayah, 5.0, bess, config, dr_lock_p_b=dr_lock
        )
        np.testing.assert_allclose(plan.schedule.p_b[10:14], 2.0, atol=1e-6)
        assert "dr" in plan.adjustment.reasons
