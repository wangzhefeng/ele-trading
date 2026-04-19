from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data.sample_data import load_default_intraday_prices, load_default_storage_config
from ele_trading.optimization.mpc_storage import run_storage_mpc
from ele_trading.utils.log_util import logger


if __name__ == '__main__':
    price_series = load_default_intraday_prices()
    storage = load_default_storage_config()
    dispatch_df = run_storage_mpc(
        prices=price_series.prices,
        horizon=4,
        initial_soc=storage.soc0,
        soc_min=storage.soc_min,
        soc_max=storage.soc_max,
        p_ch_max=storage.p_ch_max,
        p_dis_max=storage.p_dis_max,
        eta_ch=storage.eta_ch,
        eta_dis=storage.eta_dis,
        deg_cost=storage.deg_cost,
        dt=storage.dt,
    )
    logger.info('=== 输出数据和参数 ===')
    logger.info(f"price_series: {price_series}")
    logger.info(f"storage.soc0: {storage.soc0}")
    logger.info(f"storage.soc_min: {storage.soc_min}")
    logger.info(f"storage.soc_max: {storage.soc_max}")
    logger.info(f"storage.p_ch_max: {storage.p_ch_max}")
    logger.info(f"storage.p_dis_max: {storage.p_dis_max}")
    logger.info(f"storage.eta_ch: {storage.eta_ch}")
    logger.info(f"storage.eta_dis: {storage.eta_dis}")
    logger.info(f"storage.deg_cost: {storage.deg_cost}")
    logger.info(f"storage.dt: {storage.dt}")
    
    logger.info('=== 储能 MPC 滚动优化结果 ===')
    logger.info(f"result: \n{dispatch_df.to_string(index=False)}")
    logger.info(f'累计窗口目标值={dispatch_df["step_objective"].sum():.4f}')
