"""偏差考核与结算测试。"""

import numpy as np
import pandas as pd
import pytest
from ele_trading.evaluation.settlement import compute_dispatch_revenue


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
