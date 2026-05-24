"""偏差考核与结算测试。"""

import numpy as np
import pandas as pd
import pytest
from ele_trading.evaluation.settlement import compute_dispatch_revenue, compute_deviation_penalty


def _make_dispatch(n=24):
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        'price': rng.uniform(300, 500, n),
        'p_ch': np.abs(rng.normal(1.0, 0.3, n)),
        'p_dis': np.abs(rng.normal(1.0, 0.3, n)),
        'soc_next': np.clip(rng.normal(5, 1, n), 1, 10),
    })


def test_compute_dispatch_revenue_columns():
    """返回 DataFrame 应包含收益分解列。"""
    df = _make_dispatch()
    result = compute_dispatch_revenue(df, deg_cost=0.01, dt=1.0)
    for col in ('energy_arbitrage_revenue', 'degradation_cost', 'net_revenue'):
        assert col in result.columns


def test_compute_dispatch_revenue_no_modify_input():
    """不应修改原始 DataFrame。"""
    df = _make_dispatch()
    cols_before = list(df.columns)
    compute_dispatch_revenue(df, deg_cost=0.01)
    assert list(df.columns) == cols_before


def test_deviation_penalty_dead_band():
    """偏差率 ≤ 2% 时罚款应为 0。"""
    n = 6
    bid = pd.Series([10.0] * n)
    price = pd.Series([400.0] * n)
    # net_output = bid + 1%（在死区内）
    df = pd.DataFrame({
        'p_dis': [10.0 + 0.02 * 10.0] * n,
        'p_ch': [0.0] * n,
    })
    result = compute_deviation_penalty(df, bid, price, dead_band_pct=0.02)
    assert all(result['penalty'] < 1e-10)


def test_deviation_penalty_tier1():
    """偏差在 2-5% 之间按 tier1_kappa 罚款。"""
    n = 1
    bid = pd.Series([10.0])
    price = pd.Series([400.0])
    # net_output = 10.3 → dev_rate = 3%（在 tier1）
    df = pd.DataFrame({'p_dis': [10.3], 'p_ch': [0.0]})
    result = compute_deviation_penalty(df, bid, price, dt=1.0, tier1_kappa=0.25)
    assert result['penalty'].iloc[0] > 0


def test_deviation_penalty_tier2():
    """偏差 > 5% 时应同时包含 tier1 和 tier2 罚款。"""
    n = 1
    bid = pd.Series([10.0])
    price = pd.Series([400.0])
    # net_output = 10.6 → dev_rate = 6%（进入 tier2）
    df = pd.DataFrame({'p_dis': [10.6], 'p_ch': [0.0]})
    result = compute_deviation_penalty(df, bid, price, dt=1.0, tier2_kappa=0.50)
    assert result['penalty'].iloc[0] > 0


def test_deviation_penalty_zero_bid():
    """申报量为 0 时不抛异常。"""
    n = 3
    bid = pd.Series([0.0] * n)
    price = pd.Series([350.0] * n)
    df = pd.DataFrame({'p_dis': [1.0] * n, 'p_ch': [0.0] * n})
    result = compute_deviation_penalty(df, bid, price)
    assert len(result) == 3
