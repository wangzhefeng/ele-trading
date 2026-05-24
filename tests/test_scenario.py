"""场景生成与缩减测试。"""

import numpy as np
import pytest
from ele_trading.scenario.sampler import generate_price_scenarios, PriceScenario
from ele_trading.scenario.reduction import normalize_weights, reduce_scenarios

POINT_FORECAST = [300.0, 310.0, 350.0, 420.0, 500.0, 480.0]


def test_lhs_generation():
    """LHS 方法生成指定数量场景。"""
    scenarios = generate_price_scenarios(POINT_FORECAST, num_scenarios=5, method='lhs', random_seed=42)
    assert len(scenarios) == 5
    for s in scenarios:
        assert len(s.prices) == len(POINT_FORECAST)
        assert s.weight == 1.0 / 5


def test_mc_generation():
    """MC 方法向后兼容。"""
    scenarios = generate_price_scenarios(POINT_FORECAST, num_scenarios=3, method='mc', random_seed=1)
    assert len(scenarios) == 3
    for s in scenarios:
        assert all(p >= 0 for p in s.prices)


def test_correlation_matrix():
    """带时序相关矩阵的场景生成。"""
    T = len(POINT_FORECAST)
    corr = np.eye(T) + 0.3 * (1 - np.eye(T))  # 简单相关矩阵
    scenarios = generate_price_scenarios(POINT_FORECAST, num_scenarios=10, corr_matrix=corr, random_seed=99)
    assert len(scenarios) == 10


def test_invalid_num_scenarios():
    """num_scenarios <= 0 应抛出 ValueError。"""
    with pytest.raises(ValueError, match='大于 0'):
        generate_price_scenarios(POINT_FORECAST, num_scenarios=0)


def test_normalize_weights():
    """归一化后权重和应为 1。"""
    s = [
        PriceScenario(name='a', prices=[100.0], weight=2.0),
        PriceScenario(name='b', prices=[200.0], weight=3.0),
    ]
    normalized = normalize_weights(s)
    assert abs(sum(sc.weight for sc in normalized) - 1.0) < 1e-10


def test_reduce_scenarios():
    """场景缩减后数量正确且权重归一化。"""
    scenarios = generate_price_scenarios(POINT_FORECAST, num_scenarios=20, random_seed=7)
    reduced = reduce_scenarios(scenarios, top_k=5)
    assert len(reduced) == 5
    assert abs(sum(s.weight for s in reduced) - 1.0) < 1e-10


def test_reduce_scenarios_top_k_large():
    """top_k >= N 时返回原场景归一化。"""
    scenarios = generate_price_scenarios(POINT_FORECAST, num_scenarios=3, random_seed=1)
    reduced = reduce_scenarios(scenarios, top_k=5)
    assert len(reduced) == 3
