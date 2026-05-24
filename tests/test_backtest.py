"""最小回测闭环测试。"""

from ele_trading.evaluation.backtest import run_simple_backtest


def test_backtest_returns_required_fields():
    """回测返回值应包含四个必需指标字段。"""
    metrics = run_simple_backtest(horizon=4)
    for key in ('Total Revenue', 'Energy Arbitrage Revenue', 'Degradation Cost', 'Average SOC'):
        assert key in metrics


def test_backtest_values_reasonable():
    """回测指标数值合理。"""
    metrics = run_simple_backtest(horizon=4)
    # 样例数据场景下总收益应 > 0
    assert metrics['Total Revenue'] >= 0
    # 平均 SOC 应在合理范围（soc_min=1, soc_max=10）
    assert 1.0 <= metrics['Average SOC'] <= 10.0
    # 退化成本非负
    assert metrics['Degradation Cost'] >= 0
