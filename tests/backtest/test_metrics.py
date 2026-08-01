"""评估指标测试。"""

import numpy as np
import pandas as pd
from ele_trading.backtest.metrics import (
    summarize_bess_metrics,
    compute_extended_metrics,
    compute_rainflow_degradation,
)


def _make_dispatch_df(n=24):
    """构造标准测试 DataFrame。"""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        'price': rng.uniform(200, 600, n),
        'p_ch': np.abs(rng.normal(1.5, 0.5, n)),
        'p_dis': np.abs(rng.normal(1.5, 0.5, n)),
        'net_revenue': rng.normal(50, 10, n),
        'energy_arbitrage_revenue': rng.normal(60, 12, n),
        'degradation_cost': np.abs(rng.normal(10, 2, n)),
        'soc_next': np.clip(rng.normal(5, 1.5, n), 1, 10),
    })


def test_summarize_bess_metrics_fields():
    """summarize_bess_metrics 应包含四个必需字段。"""
    df = _make_dispatch_df()
    metrics = summarize_bess_metrics(df)
    for field in ('Total Revenue', 'Energy Arbitrage Revenue', 'Degradation Cost', 'Average SOC'):
        assert field in metrics
    assert isinstance(metrics['Total Revenue'], float)


def test_summarize_values_reasonable():
    """指标值应在合理范围。"""
    df = _make_dispatch_df()
    metrics = summarize_bess_metrics(df)
    assert metrics['Average SOC'] >= df['soc_next'].min()
    assert metrics['Average SOC'] <= df['soc_next'].max()


def test_extended_metrics_fields():
    """compute_extended_metrics 应返回六个字段。"""
    df = _make_dispatch_df()
    result = compute_extended_metrics(df, e_cap=10.0)
    for field in ('sharpe', 'max_drawdown', 'efc_count', 'revenue_per_efc', 'rte', 'utilization'):
        assert field in result


def test_sharpe_finite():
    """Sharpe 比率应为有限数值。"""
    df = _make_dispatch_df(48)
    result = compute_extended_metrics(df, e_cap=10.0)
    assert np.isfinite(result['sharpe'])


def test_mdd_non_positive():
    """MDD 应 ≤ 0 且 ≥ -1。"""
    df = _make_dispatch_df()
    result = compute_extended_metrics(df, e_cap=10.0)
    assert -1.0 <= result['max_drawdown'] <= 0.0 + 1e-10


def test_efc_positive():
    """有放电时 EFC 应 > 0。"""
    df = pd.DataFrame({
        'p_ch': [1.0] * 6,
        'p_dis': [2.0] * 6,
        'net_revenue': [10.0] * 6,
    })
    result = compute_extended_metrics(df, e_cap=5.0, dt=1.0)
    assert result['efc_count'] > 0


def test_utilization_in_range():
    """利用率应在 [0, 1]。"""
    df = _make_dispatch_df()
    result = compute_extended_metrics(df, e_cap=10.0)
    assert 0.0 <= result['utilization'] <= 1.0


# --- 雨流退化核算测试 ---


def test_rainflow_single_full_cycle():
    """V 形 SOC（1→0→1）由雨流识别为两个半循环，等效约 0.5 个完整循环。"""
    soc = [1.0, 0.0, 1.0]
    result = compute_rainflow_degradation(soc, e_cap=10.0)
    # rainflow 将 V 形拆为两个 count=0.5 的半循环，efc = 0.5
    assert abs(result['rainflow_efc'] - 0.5) < 0.01
    assert result['cycle_count'] == 2


def test_rainflow_multiple_cycles():
    """多次循环应累积等效循环次数。"""
    # 1.0 → 0.2 → 0.9 → 0.1 → 0.8 → 0.3 → 1.0
    soc = [1.0, 0.2, 0.9, 0.1, 0.8, 0.3, 1.0]
    result = compute_rainflow_degradation(soc, e_cap=10.0)
    assert result['rainflow_efc'] > 0.5
    assert result['cycle_count'] >= 3


def test_rainflow_degradation_cost():
    """退化成本 = rainflow_efc × deg_cost_per_cycle。"""
    soc = [1.0, 0.2, 0.9, 0.1, 0.8, 0.3, 1.0]
    cost_per_cycle = 50.0
    result = compute_rainflow_degradation(soc, e_cap=10.0, deg_cost_per_cycle=cost_per_cycle)
    expected_cost = result['rainflow_efc'] * cost_per_cycle
    assert abs(result['degradation_cost'] - expected_cost) < 0.01


def test_rainflow_fields():
    """应返回四个字段。"""
    soc = [1.0, 0.5, 0.8, 0.2, 1.0]
    result = compute_rainflow_degradation(soc, e_cap=10.0)
    for field in ('rainflow_efc', 'total_throughput', 'degradation_cost', 'cycle_count'):
        assert field in result


def test_rainflow_constant_soc():
    """恒定 SOC 应无有效循环（range=0）。"""
    soc = [0.5] * 24
    result = compute_rainflow_degradation(soc, e_cap=10.0)
    assert result['rainflow_efc'] == 0.0
    assert result['total_throughput'] == 0.0

