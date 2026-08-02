"""中长期头寸约束优化测试（v4 P0 / §7.1.2）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.positions.mid_long_optimizer import (
    MidLongOptimizationConfig,
    StrategyConfig,
    plan_mid_long,
    plan_mid_long_position_cvar,
)
from pathlib import Path

CONFIG_YAML = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "markets"
    / "single_settlement.yaml"
)

MONTHS = pd.period_range("2026-08", periods=6, freq="M").strftime("%Y-%m")
Q_LOAD = pd.Series([100.0, 110.0, 120.0, 115.0, 105.0, 95.0], index=MONTHS)
P_LONG = pd.Series([300.0, 305.0, 310.0, 308.0, 302.0, 298.0], index=MONTHS)


def _spot_scenarios(*, fat_tail: bool = False):
    """三个月度实时价格场景；fat_tail 时尾部场景极贵但概率低。"""
    base = np.array([320.0, 325.0, 330.0, 328.0, 322.0, 318.0])
    if fat_tail:
        # 期望现货 ≈ 278 < 中长期 298~310：风险中性下不买长期；
        # 尾部场景 ≈720 极大 → 风险厌恶下应提高覆盖
        scenarios = {
            "low": pd.Series(base - 120.0, index=MONTHS),
            "mid": pd.Series(base - 40.0, index=MONTHS),
            "high": pd.Series(base + 400.0, index=MONTHS),
        }
        probabilities = {"low": 0.6, "mid": 0.3, "high": 0.1}
    else:
        scenarios = {
            "low": pd.Series(base - 30.0, index=MONTHS),
            "mid": pd.Series(base, index=MONTHS),
            "high": pd.Series(base + 40.0, index=MONTHS),
        }
        probabilities = {"low": 0.3, "mid": 0.5, "high": 0.2}
    return scenarios, probabilities


def test_cvar_optimization_solves_and_respects_coverage_bounds():
    scenarios, probabilities = _spot_scenarios()
    config = MidLongOptimizationConfig(min_coverage=0.5, max_coverage=0.9)
    plan = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        scenarios,
        probabilities,
        budget=1e9,
        config=config,
    )
    ratio = plan.q_long_monthly / Q_LOAD
    assert (ratio >= 0.5 - 1e-6).all()
    assert (ratio <= 0.9 + 1e-6).all()
    assert 0.0 <= plan.coverage <= 1.0
    assert plan.expected_cost > 0.0
    assert plan.expected_risk >= plan.expected_cost  # CVaR ≥ 期望（上尾）


def test_cvar_weight_increases_coverage_under_fat_tail():
    """肥尾场景下提高 CVaR 权重 → 提高中长期覆盖以规避实时尾部风险。"""
    scenarios, probabilities = _spot_scenarios(fat_tail=True)
    risk_neutral = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        scenarios,
        probabilities,
        budget=1e9,
        config=MidLongOptimizationConfig(cvar_weight=0.0),
    )
    risk_averse = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        scenarios,
        probabilities,
        budget=1e9,
        config=MidLongOptimizationConfig(cvar_weight=5.0),
    )
    assert risk_averse.coverage >= risk_neutral.coverage - 1e-6
    assert risk_averse.coverage > risk_neutral.coverage + 0.01


def test_turnover_penalty_pulls_toward_previous_position():
    scenarios, probabilities = _spot_scenarios()
    prev = Q_LOAD * 0.6
    free = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        scenarios,
        probabilities,
        budget=1e9,
        config=MidLongOptimizationConfig(turnover_penalty=0.0),
        q_long_prev=prev,
    )
    sticky = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        scenarios,
        probabilities,
        budget=1e9,
        config=MidLongOptimizationConfig(turnover_penalty=100.0),
        q_long_prev=prev,
    )
    dev_free = float((free.q_long_monthly - prev).abs().sum())
    dev_sticky = float((sticky.q_long_monthly - prev).abs().sum())
    assert dev_sticky < dev_free


def test_total_long_cap_binds_when_spot_expensive():
    # 现货整体比中长期贵 → 纯成本口径下最优为全覆盖；
    # 年度合约总量上限（v4 §7.1.2 公式中的 annual_budget）约束覆盖
    base = np.array([320.0, 325.0, 330.0, 328.0, 322.0, 318.0])
    expensive_spot = {
        "low": pd.Series(base + 5.0, index=MONTHS),
        "mid": pd.Series(base + 40.0, index=MONTHS),
        "high": pd.Series(base + 80.0, index=MONTHS),
    }
    probabilities = {"low": 0.5, "mid": 0.3, "high": 0.2}
    cfg = MidLongOptimizationConfig(min_coverage=0.0, cvar_weight=0.0)
    loose = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        expensive_spot,
        probabilities,
        budget=1e9,
        config=cfg,
    )
    assert loose.coverage > 0.99  # 无上限 → 全覆盖

    cap_mwh = 300.0
    tight = plan_mid_long_position_cvar(
        Q_LOAD,
        P_LONG,
        expensive_spot,
        probabilities,
        budget=1e9,
        config=MidLongOptimizationConfig(
            min_coverage=0.0, cvar_weight=0.0, max_total_long_mwh=cap_mwh
        ),
    )
    assert tight.q_long_monthly.sum() <= cap_mwh + 1e-3
    assert 0.0 < tight.coverage < 1.0
    assert tight.coverage < loose.coverage


def test_invalid_config_rejected():
    with pytest.raises(ValueError, match="cvar_alpha"):
        MidLongOptimizationConfig(cvar_alpha=1.5)
    with pytest.raises(ValueError, match="coverage"):
        MidLongOptimizationConfig(min_coverage=0.9, max_coverage=0.5)


def test_strategy_router_defaults_to_heuristic():
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    p_spot = pd.Series([320.0] * 6, index=MONTHS)
    plan = plan_mid_long(
        Q_LOAD,
        P_LONG,
        p_spot,
        budget=1e9,
        config=config,
    )
    # 启发式路径：alpha_long 在默认区间 [0.7, 0.9]
    assert 0.7 - 1e-9 <= plan.alpha_long <= 0.9 + 1e-9


def test_strategy_router_requires_scenarios_for_optimization():
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    p_spot = pd.Series([320.0] * 6, index=MONTHS)
    with pytest.raises(ValueError, match="spot_scenarios"):
        plan_mid_long(
            Q_LOAD,
            P_LONG,
            p_spot,
            budget=1e9,
            config=config,
            strategy=StrategyConfig(mid_long_strategy="cvar_optimization"),
        )
