from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data.sample_data import load_default_day_ahead_prices, load_default_storage_config
from ele_trading.optimization.storage_arbitrage import solve_storage_arbitrage
from ele_trading.utils.log_util import logger


if __name__ == '__main__':
    # 未来分时电价
    price_series = load_default_day_ahead_prices()
    # 储能配置参数
    storage = load_default_storage_config()
    result = solve_storage_arbitrage(
        prices=price_series.prices,
        soc0=storage.soc0,
        soc_min=storage.soc_min,
        soc_max=storage.soc_max,
        p_ch_max=storage.p_ch_max,
        p_dis_max=storage.p_dis_max,
        eta_ch=storage.eta_ch,
        eta_dis=storage.eta_dis,
        deg_cost=storage.deg_cost,
        dt=storage.dt,
        enforce_terminal_soc=False,
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
    
    logger.info('=== 储能单市场套利结果 ===')
    logger.info(f"objective={result['objective']:.4f}")
    logger.info(f"p_ch={[round(x, 4) for x in result['p_ch']]}")
    logger.info(f"p_dis={[round(x, 4) for x in result['p_dis']]}")
    logger.info(f"soc={[round(x, 4) for x in result['soc']]}")
