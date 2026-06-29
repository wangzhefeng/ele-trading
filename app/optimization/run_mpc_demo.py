from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data_provider.sample_data import load_default_intraday_prices, load_default_bess_config
from ele_trading.optimization.mpc_bess import run_bess_mpc
from ele_trading.utils.log_util import logger


if __name__ == '__main__':
    price_series = load_default_intraday_prices()
    bess = load_default_bess_config()
    dispatch_df = run_bess_mpc(
        prices=price_series.prices,
        horizon=4,
        initial_soc=bess.soc0,
        soc_min=bess.soc_min,
        soc_max=bess.soc_max,
        p_ch_max=bess.p_ch_max,
        p_dis_max=bess.p_dis_max,
        eta_ch=bess.eta_ch,
        eta_dis=bess.eta_dis,
        deg_cost=bess.deg_cost,
        dt=bess.dt,
    )
    logger.info('=== 输出数据和参数 ===')
    logger.info(f"price_series: {price_series}")
    logger.info(f"bess.soc0: {bess.soc0}")
    logger.info(f"bess.soc_min: {bess.soc_min}")
    logger.info(f"bess.soc_max: {bess.soc_max}")
    logger.info(f"bess.p_ch_max: {bess.p_ch_max}")
    logger.info(f"bess.p_dis_max: {bess.p_dis_max}")
    logger.info(f"bess.eta_ch: {bess.eta_ch}")
    logger.info(f"bess.eta_dis: {bess.eta_dis}")
    logger.info(f"bess.deg_cost: {bess.deg_cost}")
    logger.info(f"bess.dt: {bess.dt}")
    
    logger.info('=== 储能 MPC 滚动优化结果 ===')
    logger.info(f"result: \n{dispatch_df.to_string(index=False)}")
    logger.info(f'累计窗口目标值={dispatch_df["step_objective"].sum():.4f}')
