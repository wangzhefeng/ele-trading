from __future__ import annotations

from ele_trading.control.rolling_dispatch import run_bess_rolling_dispatch
from ele_trading.data_provider.sample_data import load_default_intraday_prices, load_default_bess_config
from ele_trading.evaluation.metrics import summarize_bess_metrics
from ele_trading.evaluation.settlement import compute_dispatch_revenue


def run_simple_backtest(horizon: int = 4) -> dict[str, float]:
    """运行最小回测闭环。"""
    price_series = load_default_intraday_prices()
    bess_config = load_default_bess_config()

    dispatch_df = run_bess_rolling_dispatch(
        prices=price_series.prices,
        horizon=horizon,
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
    return summarize_bess_metrics(result_df)
