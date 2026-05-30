"""扩展指标接入回测测试。"""

import numpy as np
from ele_trading.control.rolling_dispatch import run_bess_rolling_dispatch
from ele_trading.data_provider.sample_data import load_default_intraday_prices, load_default_bess_config
from ele_trading.evaluation.settlement import compute_dispatch_revenue
from ele_trading.evaluation.metrics import compute_extended_metrics


def test_extended_metrics_on_backtest():
    """在完整 MPC 回测上计算扩展指标。"""
    price_series = load_default_intraday_prices()
    bess_config = load_default_bess_config()

    dispatch_df = run_bess_rolling_dispatch(
        prices=price_series.prices,
        horizon=4,
        initial_soc=bess_config.soc0,
        soc_min=bess_config.soc_min,
        soc_max=bess_config.soc_max,
        p_ch_max=bess_config.p_ch_max,
        p_dis_max=bess_config.p_dis_max,
        eta_ch=bess_config.eta_ch,
        eta_dis=bess_config.eta_dis,
        deg_cost=bess_config.deg_cost,
        dt=bess_config.dt,
    )
    result_df = compute_dispatch_revenue(dispatch_df, deg_cost=bess_config.deg_cost, dt=bess_config.dt)
    extended = compute_extended_metrics(
        result_df,
        e_cap=bess_config.soc_max - bess_config.soc_min,  # 可用容量
        dt=bess_config.dt,
    )
    assert extended['efc_count'] >= 0
    assert extended['max_drawdown'] <= 0.0 + 1e-10
    assert 0.0 <= extended['utilization'] <= 1.0
    assert np.isfinite(extended['sharpe'])
