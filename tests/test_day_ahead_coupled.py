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


class TestConstraints:
    def test_no_reverse_power(self, bess, config):
        """不可倒送：任意刻 Q_load + (p_bc-p_bd)*step ≥ -1e-9（§14.1）。"""
        horizon = 96
        q_load = np.full(horizon, 1.0)  # 小负荷，储能大功率放电会顶到倒送约束
        p_dayah = np.full(horizon, 300.0)
        p_real = np.concatenate([np.full(48, 200.0), np.full(48, 400.0)])

        for mode in ("A", "B", "C"):
            plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode=mode)
            net_load = q_load + (plan.p_bc - plan.p_bd) * 0.25
            assert net_load.min() >= -1e-9, f"mode {mode} violates no-reverse"

    def test_nan_input_rejected(self, bess, config):
        """预测含 NaN 时报错而非静默填充（§11.4.2）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        q_load[10] = np.nan
        p_dayah = np.full(horizon, 300.0)
        p_real = np.full(horizon, 300.0)

        with pytest.raises(ValueError, match="NaN"):
            solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")

    def test_exclusive_charge_discharge(self, bess, config):
        """充放互斥：exclusive=true 时最优解不同时充放（§14.1）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.concatenate([np.full(48, 200.0), np.full(48, 400.0)])

        config.exclusive_charge_discharge = True
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        simultaneous = (plan.p_bc > 1e-6) & (plan.p_bd > 1e-6)
        assert not simultaneous.any()

    def test_lp_no_simultaneous_when_eff_lt_1(self, bess, config):
        """exclusive=false 且效率<1 且价非负时，LP 最优解亦不同时充放（§14.1）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.concatenate([np.full(48, 200.0), np.full(48, 400.0)])

        config.exclusive_charge_discharge = False
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="A")
        simultaneous = (plan.p_bc > 1e-6) & (plan.p_bd > 1e-6)
        assert not simultaneous.any()

    def test_no_discharge_on_curtail(self, bess, config):
        """限电时段禁放：t_curt 内 p_bd=0（§2.3 可选约束）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.full(horizon, 400.0)  # 高价，本应有放电激励
        t_curt = list(range(60, 72))

        config.no_discharge_on_curtail = True
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="A", t_curt=t_curt)
        assert plan.p_bd[t_curt].max() < 1e-9

    def test_constraint_flags_populated(self, bess, config):
        """约束提示：SOC 触限与倒送激活时段应被记录（§6.5）。"""
        horizon = 96
        q_load = np.full(horizon, 1.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.concatenate([np.full(48, 200.0), np.full(48, 400.0)])

        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="A")
        assert isinstance(plan.constraint_flags, dict)
        # 小负荷 + 大功率放电 → 倒送约束应在部分时段激活
        assert "no_reverse_active" in plan.constraint_flags


class TestRiskClipping:
    def test_max_step_ratio_limits_jump(self, bess, config):
        """风控规则 1：相邻刻申报量变化 ≤ max_step_ratio * Q_base（§6.4）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        # 日前/实时价差交替触发多报/少报 → 规则量锯齿
        p_dayah = np.where(np.arange(horizon) % 2 == 0, 400.0, 200.0)
        p_real = np.full(horizon, 300.0)

        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        q_base = q_load - plan.p_b * 0.25
        diffs = np.abs(np.diff(plan.q_dayah))
        limits = config.risk_max_step_ratio * q_base[1:]
        assert (diffs <= limits + 1e-6).all()

    def test_daily_qty_band_limits_total(self, bess, config):
        """风控规则 2：日申报总量 ∈ ΣQ_base × [1-band, 1+band]（§6.4）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 200.0)  # 日前远便宜 → 全部多报
        p_real = np.full(horizon, 300.0)
        config.bias_k = 3  # lam_u^3 ≈ 1.16，超出 +15% 带

        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        q_base = q_load - plan.p_b * 0.25
        total_base = q_base.sum()
        assert plan.q_dayah.sum() <= total_base * (1 + config.risk_daily_qty_band) + 1e-6

    def test_long_band_check_warns_only(self, bess, config, caplog):
        """风控规则 3：申报+Q_long 越带时仅告警不强制（§6.4）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        q_long = np.full(horizon, 9.5)  # 覆盖率已贴近带边界
        p_dayah = np.full(horizon, 200.0)
        p_real = np.full(horizon, 300.0)

        import logging
        with caplog.at_level(logging.WARNING):
            plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B", q_long=q_long)
        # 只告警不修改：申报量不被压到带内
        assert plan.q_dayah.sum() > 0

    def test_bid_prices_none_when_no_reporting(self, bess, config):
        """报量不报价（默认）：bid_prices 置 None（§6.5，TODO(rule-confirm) #3）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 300.0)
        p_real = np.full(horizon, 300.0)

        config.dayahead_price_reporting = False
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        assert plan.bid_prices is None

    def test_bid_prices_clipped_when_reporting(self, bess, config):
        """报量报价时：申报价裁剪到 [price_floor, price_cap]（§6.5）。"""
        horizon = 96
        q_load = np.full(horizon, 10.0)
        p_dayah = np.concatenate([np.full(48, -20.0), np.full(48, 2000.0)])
        p_real = np.full(horizon, 300.0)

        config.dayahead_price_reporting = True
        plan = solve_day_ahead_coupled(q_load, p_dayah, p_real, bess, config, mode="B")
        assert plan.bid_prices is not None
        assert plan.bid_prices.min() >= config.price_floor
        assert plan.bid_prices.max() <= config.price_cap

    def test_mode_c_fusion_fallback(self, bess, config):
        """模式 C：优化量无效（NaN）时回退规则量（§6.4）。"""
        from ele_trading.trading.day_ahead_coupled import _fuse_mode_c_bid

        horizon = 96
        q_base = np.full(horizon, 10.0)
        p_dayah = np.full(horizon, 400.0)
        p_real = np.full(horizon, 300.0)

        q_opt_bad = np.full(horizon, np.nan)
        fused = _fuse_mode_c_bid(q_opt_bad, q_base, p_dayah, p_real, config)
        # 回退到规则量：日前偏贵 → 少报
        assert np.isfinite(fused).all()
        assert fused.mean() < q_base.mean()
