from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np

from ele_trading.control.rolling_dispatch import run_bess_rolling_dispatch
from ele_trading.data_provider.sample_data import load_default_intraday_prices, load_default_bess_config
from ele_trading.evaluation.metrics import summarize_bess_metrics, compute_rainflow_degradation
from ele_trading.evaluation.settlement import compute_dispatch_revenue
from ele_trading.utils.log_util import logger


if __name__ == '__main__':
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
    metrics = summarize_bess_metrics(result_df)

    logger.info('=== 最小回测结果 ===')
    for key, value in metrics.items():
        logger.info(f'{key}: {value:.4f}')

    # 雨流退化核算（与线性吞吐量退化并列对比）
    soc_series = result_df['soc_next'].to_numpy(dtype=float)
    rainflow_result = compute_rainflow_degradation(
        soc_series=soc_series,
        e_cap=bess_config.soc_max,  # 用 soc_max 近似 e_cap
        deg_cost_per_cycle=bess_config.deg_cost,
    )
    logger.info('=== 雨流退化核算 ===')
    logger.info(f"线性退化成本: {metrics['Degradation Cost']:.4f} CNY")
    logger.info(f"雨流退化成本: {rainflow_result['degradation_cost']:.4f} CNY")
    logger.info(f"雨流等效循环: {rainflow_result['rainflow_efc']:.4f}")
    logger.info(f"雨流吞吐量:   {rainflow_result['total_throughput']:.4f} MWh")
    logger.info(f"雨流循环数:   {rainflow_result['cycle_count']}")
