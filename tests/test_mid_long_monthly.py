"""Unit tests for mid-long-term planning and monthly trading."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.trading.contracts import MarketConfig
from ele_trading.trading.mid_long_planner import plan_mid_long_position
from ele_trading.trading.monthly_trader import build_bid_ladder, rebalance_position_gap


@pytest.fixture
def config():
    return MarketConfig()


class TestMidLongPlanner:
    def test_basic_plan(self, config):
        """Basic mid-long plan should return valid structure."""
        months = pd.period_range("2026-01", periods=12, freq="M")
        q_load = pd.Series(np.full(12, 1000.0), index=months)
        p_long = pd.Series(np.full(12, 300.0), index=months)
        p_spot = pd.Series(np.full(12, 350.0), index=months)
        budget = 5e6

        plan = plan_mid_long_position(q_load, p_long, p_spot, budget, config)
        assert 0.7 <= plan.alpha_long <= 0.9
        assert plan.alpha_dayah > 0
        assert plan.alpha_real > 0
        assert abs(plan.alpha_long + plan.alpha_dayah + plan.alpha_real - 1.0) < 1e-6
        assert len(plan.q_long_monthly) == 12
        assert plan.budget_used > 0

    def test_spot_expensive_favors_long(self, config):
        """When spot >> long, should favor higher alpha_long."""
        months = pd.period_range("2026-01", periods=12, freq="M")
        q_load = pd.Series(np.full(12, 1000.0), index=months)
        p_long = pd.Series(np.full(12, 250.0), index=months)
        p_spot = pd.Series(np.full(12, 400.0), index=months)  # spot much higher
        budget = 5e6

        plan = plan_mid_long_position(q_load, p_long, p_spot, budget, config)
        assert plan.alpha_long > 0.8  # should favor mid-long


class TestMonthlyTrader:
    def test_buy_ladder(self, config):
        """Buy ladder should have decreasing prices."""
        ladder = build_bid_ladder(
            q_low=100.0, q_high=200.0,
            p_low=280.0, p_high=320.0,
            k=5, direction="buy", config=config
        )
        assert ladder.direction == "buy"
        assert len(ladder.bid_qty) == 5
        assert len(ladder.bid_price) == 5
        # Buy: price should decrease with quantity
        assert ladder.bid_price[0] > ladder.bid_price[-1]
        # Quantity should increase
        assert ladder.bid_qty[0] < ladder.bid_qty[-1]

    def test_sell_ladder(self, config):
        """Sell ladder should have increasing prices."""
        ladder = build_bid_ladder(
            q_low=100.0, q_high=200.0,
            p_low=280.0, p_high=320.0,
            k=5, direction="sell", config=config
        )
        assert ladder.direction == "sell"
        # Sell: price should increase with quantity
        assert ladder.bid_price[0] < ladder.bid_price[-1]

    def test_price_clipping(self, config):
        """Prices should be clipped to market limits."""
        config.price_floor = 300.0
        config.price_cap = 310.0
        ladder = build_bid_ladder(
            q_low=100.0, q_high=200.0,
            p_low=280.0, p_high=320.0,
            k=5, direction="buy", config=config
        )
        assert all(300.0 <= p <= 310.0 for p in ladder.bid_price)

    def test_position_rebalancing(self, config):
        """Position rebalancing should identify buy/sell/hold actions."""
        gap = np.array([-10.0, -5.0, 0.0, 5.0, 10.0])
        pos_tol = 3.0

        result = rebalance_position_gap(gap, pos_tol, config)
        advice = result["advice"]
        assert advice[0]["action"] == "buy"  # large negative gap
        assert advice[1]["action"] == "buy"  # small negative gap
        assert advice[2]["action"] == "hold"  # within tolerance
        assert advice[3]["action"] == "sell"  # small positive gap
        assert advice[4]["action"] == "sell"  # large positive gap
        assert result["num_adjustments"] == 4
