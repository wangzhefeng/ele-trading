import numpy as np
from ele_trading.scenario.sampler import generate_price_scenarios
from ele_trading.scenario.reduction import reduce_scenarios


def test_lhs_generates_correct_count():
    base = [300.0] * 24
    scenarios = generate_price_scenarios(base, num_scenarios=10, random_seed=42)
    assert len(scenarios) == 10
    assert all(s.weight > 0 for s in scenarios)
    assert abs(sum(s.weight for s in scenarios) - 1.0) < 1e-9


def test_lhs_stratification():
    """LHS 生成的场景应比等量纯随机样本分布更均匀（均值贴近基准）。"""
    base = [300.0] * 4
    n = 50
    scenarios_lhs = generate_price_scenarios(base, num_scenarios=n, random_seed=0, method='lhs')
    means = [sum(s.prices) / len(s.prices) for s in scenarios_lhs]
    assert abs(np.mean(means) - 300.0) < 30.0


def test_mc_backward_compat():
    """method='mc' 应保持向后兼容，仍返回正确数量场景。"""
    base = [300.0] * 8
    scenarios = generate_price_scenarios(base, num_scenarios=5, random_seed=7, method='mc')
    assert len(scenarios) == 5
    assert abs(sum(s.weight for s in scenarios) - 1.0) < 1e-9


def test_kantorovich_reduction_reduces_count():
    base = [300.0] * 8
    scenarios = generate_price_scenarios(base, num_scenarios=20, random_seed=7)
    reduced = reduce_scenarios(scenarios, top_k=5)
    assert len(reduced) == 5
    assert abs(sum(s.weight for s in reduced) - 1.0) < 1e-9


def test_kantorovich_reduction_preserves_diversity():
    """缩减后保留的场景应覆盖原始场景的价格区间，不坍缩到均值附近。"""
    base = [300.0] * 4
    scenarios = generate_price_scenarios(base, num_scenarios=30, random_seed=3)
    reduced = reduce_scenarios(scenarios, top_k=5)
    all_means = [sum(s.prices) / len(s.prices) for s in reduced]
    assert max(all_means) - min(all_means) > 5.0


def test_reduction_no_op_when_already_small():
    base = [300.0] * 4
    scenarios = generate_price_scenarios(base, num_scenarios=3, random_seed=0)
    reduced = reduce_scenarios(scenarios, top_k=5)
    assert len(reduced) == 3
