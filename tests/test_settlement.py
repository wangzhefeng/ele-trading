import pandas as pd
from ele_trading.evaluation.settlement import compute_dispatch_revenue, compute_deviation_penalty


def _make_df(p_dis, p_ch, price=300.0):
    return pd.DataFrame({'p_dis': p_dis, 'p_ch': p_ch, 'price': [price] * len(p_dis)})


def test_compute_dispatch_revenue_keys():
    df = _make_df([2.0, 0.0], [0.0, 2.0])
    result = compute_dispatch_revenue(df, deg_cost=0.01)
    for col in ('energy_arbitrage_revenue', 'degradation_cost', 'net_revenue'):
        assert col in result.columns


def test_deviation_penalty_dead_band():
    """偏差率 0%（死区内），罚款应为 0。"""
    dispatch_df = pd.DataFrame({'p_dis': [1.0], 'p_ch': [0.0]})
    bid_series = pd.Series([1.0])
    pi_da = pd.Series([300.0])
    penalty_df = compute_deviation_penalty(dispatch_df, bid_series, pi_da)
    assert abs(penalty_df['penalty'].iloc[0]) < 1e-9


def test_deviation_penalty_within_dead_band():
    """偏差率 1%（≤2% 死区），罚款仍为 0。"""
    dispatch_df = pd.DataFrame({'p_dis': [101.0], 'p_ch': [0.0]})
    bid_series = pd.Series([100.0])
    pi_da = pd.Series([300.0])
    penalty_df = compute_deviation_penalty(dispatch_df, bid_series, pi_da, dt=1.0)
    assert abs(penalty_df['penalty'].iloc[0]) < 1e-9


def test_deviation_penalty_tier1():
    """偏差率 3%（在 2%–5% 区间），按 0.25 系数惩罚。"""
    bid = 100.0
    actual = 103.0  # +3% 超发，偏差电量 3 MWh
    dispatch_df = pd.DataFrame({'p_dis': [actual], 'p_ch': [0.0]})
    bid_series = pd.Series([bid])
    pi_da = pd.Series([300.0])
    penalty_df = compute_deviation_penalty(dispatch_df, bid_series, pi_da, dt=1.0)
    # 超出死区(2 MWh)后的 1 MWh 在 tier1，罚款 = 1 * 0.25 * 300 = 75
    expected = 1.0 * 0.25 * 300.0
    assert abs(penalty_df['penalty'].iloc[0] - expected) < 1e-4


def test_deviation_penalty_tier2():
    """偏差率 10%（>5%），tier1 + tier2 分段惩罚。"""
    bid = 100.0
    actual = 110.0  # +10% 超发，偏差电量 10 MWh
    dispatch_df = pd.DataFrame({'p_dis': [actual], 'p_ch': [0.0]})
    bid_series = pd.Series([bid])
    pi_da = pd.Series([300.0])
    penalty_df = compute_deviation_penalty(dispatch_df, bid_series, pi_da, dt=1.0)
    # 死区 2 MWh 无罚；tier1(2–5 MWh) 3 MWh * 0.25 * 300 = 225；
    # tier2(>5 MWh) 5 MWh * 0.50 * 300 = 750；合计 = 975
    expected = 3.0 * 0.25 * 300.0 + 5.0 * 0.50 * 300.0
    assert abs(penalty_df['penalty'].iloc[0] - expected) < 1e-4
