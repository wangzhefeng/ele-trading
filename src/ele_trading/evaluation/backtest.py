from __future__ import annotations

from ele_trading.control.rolling_dispatch import run_storage_rolling_dispatch
from ele_trading.data_provider.sample_data import load_default_intraday_prices, load_default_storage_config
from ele_trading.evaluation.metrics import summarize_storage_metrics
from ele_trading.evaluation.settlement import compute_dispatch_revenue


def run_simple_backtest(horizon: int = 4) -> dict[str, float]:
    """运行最小回测闭环。"""
    price_series = load_default_intraday_prices()
    storage_config = load_default_storage_config()

    dispatch_df = run_storage_rolling_dispatch(
        prices=price_series.prices,
        horizon=horizon,
        initial_soc=storage_config.soc0,
        soc_min=storage_config.soc_min,
        soc_max=storage_config.soc_max,
        p_ch_max=storage_config.p_ch_max,
        p_dis_max=storage_config.p_dis_max,
        eta_ch=storage_config.eta_ch,
        eta_dis=storage_config.eta_dis,
        deg_cost=storage_config.deg_cost,
        dt=storage_config.dt,
    )
    result_df = compute_dispatch_revenue(dispatch_df, deg_cost=storage_config.deg_cost, dt=storage_config.dt)
    return summarize_storage_metrics(result_df)
