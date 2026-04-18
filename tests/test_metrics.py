import math
import pandas as pd
from ele_trading.evaluation.metrics import summarize_storage_metrics, compute_extended_metrics


def _make_dispatch_df():
    """构造最小测试 DataFrame：8 步，交替充放电。"""
    rows = []
    for cycle in range(2):
        # 充电两步（低价），放电两步（高价）
        for _ in range(2):
            rows.append({'p_ch': 2.0, 'p_dis': 0.0, 'price': 200.0, 'soc_next': 7.0,
                         'energy_arbitrage_revenue': -400.0,
                         'degradation_cost': 0.02, 'net_revenue': -400.02})
        for _ in range(2):
            rows.append({'p_ch': 0.0, 'p_dis': 2.0, 'price': 400.0, 'soc_next': 5.0,
                         'energy_arbitrage_revenue': 800.0,
                         'degradation_cost': 0.02, 'net_revenue': 799.98})
    df = pd.DataFrame(rows)
    df['step'] = range(len(df))
    return df


def test_summarize_storage_metrics_keys():
    df = _make_dispatch_df()
    result = summarize_storage_metrics(df)
    assert 'Total Revenue' in result
    assert 'Average SOC' in result


def test_compute_extended_metrics_sharpe():
    df = _make_dispatch_df()
    metrics = compute_extended_metrics(df, e_cap=10.0, dt=1.0)
    assert 'sharpe' in metrics
    assert math.isfinite(metrics['sharpe'])


def test_compute_extended_metrics_mdd():
    df = _make_dispatch_df()
    metrics = compute_extended_metrics(df, e_cap=10.0, dt=1.0)
    assert 'max_drawdown' in metrics
    assert metrics['max_drawdown'] <= 0.0


def test_compute_extended_metrics_efc():
    df = _make_dispatch_df()
    metrics = compute_extended_metrics(df, e_cap=10.0, dt=1.0)
    assert 'efc_count' in metrics
    # EFC = Σ p_dis*dt / E_cap = (0+0+2+2)*2 * 1 / 10 = 8/10 = 0.8
    assert abs(metrics['efc_count'] - 0.8) < 1e-6


def test_compute_extended_metrics_rte():
    df = _make_dispatch_df()
    metrics = compute_extended_metrics(df, e_cap=10.0, dt=1.0, eta_ch=0.95, eta_dis=0.95)
    assert 'rte' in metrics
    assert abs(metrics['rte'] - 0.95 * 0.95) < 1e-9


def test_compute_extended_metrics_utilization():
    df = _make_dispatch_df()
    metrics = compute_extended_metrics(df, e_cap=10.0, dt=1.0)
    assert 'utilization' in metrics
    assert 0.0 <= metrics['utilization'] <= 1.0


def test_compute_extended_metrics_revenue_per_efc():
    df = _make_dispatch_df()
    metrics = compute_extended_metrics(df, e_cap=10.0, dt=1.0)
    assert 'revenue_per_efc' in metrics
    assert math.isfinite(metrics['revenue_per_efc'])
