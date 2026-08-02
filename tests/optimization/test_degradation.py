"""Level 1 退化模型测试（v4 P0 / §6.1.2）。"""

from __future__ import annotations

import numpy as np
import pytest

from ele_trading.optimization.bess_arbitrage import solve_bess_arbitrage

# 高低价差明显的样例价格（与 M1 等价验证同序列）
PRICES = [
    330.0, 315.0, 300.0, 295.0, 320.0, 355.0,
    410.0, 470.0, 520.0, 560.0, 500.0, 430.0,
]


def test_level1_lp_solves_with_valid_schedule():
    """Level 1 退化 LP 可解，SOC 轨迹在物理界限内。"""
    result = solve_bess_arbitrage(
        PRICES,
        dt=0.25,
        degradation="level1",
        deg_calendar_cost_per_hour=2.0,
        deg_cycle_cost_per_mwh=15.0,
    )
    soc = np.asarray(result["soc"])
    assert np.isfinite(soc).all()
    assert (soc >= 1.0 - 1e-6).all()
    assert (soc <= 10.0 + 1e-6).all()


def test_level1_differs_quantifiably_from_linear():
    """两种退化模型的目标值差异可量化（Level 1 日历项使成本更高）。"""
    linear = solve_bess_arbitrage(PRICES, dt=0.25, deg_cost=0.01)
    level1 = solve_bess_arbitrage(
        PRICES,
        dt=0.25,
        degradation="level1",
        deg_calendar_cost_per_hour=2.0,
        deg_cycle_cost_per_mwh=15.0,
    )
    # 差异必须非零且方向合理：Level 1 的退化口径更贵 → 净收益更低
    gap = linear["objective"] - level1["objective"]
    assert gap > 0.0


def test_level1_cycle_cost_suppresses_cycling():
    """高循环成本应减少 SOC 摆幅（行为可解释）。"""
    mild = solve_bess_arbitrage(
        PRICES,
        dt=0.25,
        degradation="level1",
        deg_cycle_cost_per_mwh=1.0,
    )
    harsh = solve_bess_arbitrage(
        PRICES,
        dt=0.25,
        degradation="level1",
        deg_cycle_cost_per_mwh=500.0,
    )
    swing_mild = float(
        np.sum(np.abs(np.diff([5.0, *mild["soc"]])))
    )
    swing_harsh = float(
        np.sum(np.abs(np.diff([5.0, *harsh["soc"]])))
    )
    assert swing_harsh <= swing_mild + 1e-6


def test_linear_remains_default_and_unchanged():
    """默认行为不变：不显式指定即为 Level 0 线性退化。"""
    default = solve_bess_arbitrage(PRICES, dt=0.25, deg_cost=0.01)
    explicit = solve_bess_arbitrage(
        PRICES, dt=0.25, deg_cost=0.01, degradation="linear"
    )
    assert default["objective"] == pytest.approx(explicit["objective"])
    assert default["soc"] == pytest.approx(explicit["soc"])


def test_level1_rejects_negative_costs():
    with pytest.raises(ValueError, match="non-negative"):
        solve_bess_arbitrage(
            PRICES,
            dt=0.25,
            degradation="level1",
            deg_cycle_cost_per_mwh=-1.0,
        )


def test_unknown_degradation_model_rejected():
    with pytest.raises(ValueError, match="unknown degradation"):
        solve_bess_arbitrage(PRICES, dt=0.25, degradation="level2")
