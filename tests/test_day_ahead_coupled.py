"""Unit tests for day-ahead coupled optimization."""

from __future__ import annotations

import numpy as np
import pytest

from ele_trading.trading.contracts import MarketConfig
from ele_trading.trading.day_ahead_coupled import solve_day_ahead_coupled


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


class TestModeA:
    def test_arbitrage_with_spread(self, bess, config):
        """Mode A should arbitrage when real price varies."""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        # Real price: low first half, high second half
        p_real = np.concatenate([np.full(48, 200.0), np.full(48, 400.0)])

        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="A")
        assert plan.p_bc.sum() > 0  # should charge in cheap periods
        assert plan.p_bd.sum() > 0  # should discharge in expensive periods

    def test_no_arbitrage_flat_price(self, bess, config):
        """Mode A should not arbitrage when price is flat."""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.full(horizon, 300.0)

        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="A")
        assert plan.p_bc.sum() == pytest.approx(0.0, abs=1e-6)
        assert plan.p_bd.sum() == pytest.approx(0.0, abs=1e-6)


class TestModeB:
    def test_effective_price_direction(self, bess, config):
        """Mode B π_eff should follow §7.2 direction (A4 fix)."""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        # Case 1: real > dayah → use lam_l (favor day-ahead)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.full(horizon, 350.0)
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        # Should bid more than base load (lam_u^k > 1 in cheap day-ahead)
        assert plan.q_dayah.mean() > q_load.mean()

        # Case 2: dayah > real → use lam_u (favor real-time)
        p_dayah = np.full(horizon, 350.0)
        p_real = np.full(horizon, 300.0)
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        # Should bid less than base load (lam_l^k < 1 in expensive day-ahead)
        assert plan.q_dayah.mean() < q_load.mean()

    def test_terminal_soc_constraint(self, bess, config):
        """Mode B should respect terminal SOC constraint."""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.concatenate([np.full(48, 200.0), np.full(48, 400.0)])

        config.soc_terminal_min = 5.0  # require end at initial
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        assert plan.soc[-1] >= 5.0 - 1e-6


class TestModeC:
    def test_joint_optimization(self, bess, config):
        """Mode C should jointly optimize bid and storage."""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.concatenate([np.full(48, 250.0), np.full(48, 400.0)])
        p_real = np.full(horizon, 300.0)

        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="C")
        assert plan.p_bc.sum() > 0
        assert plan.p_bd.sum() > 0
        assert plan.q_dayah.shape == (horizon,)

    def test_penalty_linearization(self, bess, config):
        """Mode C penalty should be non-negative."""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.full(horizon, 350.0)

        config.w_pen = 10.0  # high penalty weight
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="C")
        # With high penalty, bid should be close to expected real load
        q_real_expected = q_load + (plan.p_bc - plan.p_bd) * 0.25
        deviation = np.abs(plan.q_dayah - q_real_expected)
        assert deviation.mean() < 2.0  # should track real load closely


class TestPerformance:
    def test_mode_b_speed(self, bess, config):
        """Mode B 96-point LP should solve in ≤5s."""
        import time

        horizon = 96
        q_load = np.random.default_rng(42).uniform(5, 15, horizon)
        p_dayah = np.random.default_rng(43).uniform(250, 350, horizon)
        p_real = np.random.default_rng(44).uniform(250, 350, horizon)

        start = time.time()
        solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        elapsed = time.time() - start
        assert elapsed < 5.0
