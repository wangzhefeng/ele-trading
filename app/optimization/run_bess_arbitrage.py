from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data_provider.sample_data import load_default_day_ahead_prices, load_default_bess_config
from ele_trading.optimization.bess_arbitrage import solve_bess_arbitrage
from ele_trading.utils.log_util import logger


if __name__ == '__main__':
    # 未来分时电价
    price_series = load_default_day_ahead_prices()
    # 储能配置参数
    bess = load_default_bess_config()
    result = solve_bess_arbitrage(
        prices=price_series.prices,
        soc0=bess.soc0,
        soc_min=bess.soc_min,
        soc_max=bess.soc_max,
        p_ch_max=bess.p_ch_max,
        p_dis_max=bess.p_dis_max,
        eta_ch=bess.eta_ch,
        eta_dis=bess.eta_dis,
        deg_cost=bess.deg_cost,
        dt=bess.dt,
        enforce_terminal_soc=False,
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
    
    logger.info('=== 储能单市场套利结果 ===')
    logger.info(f"objective={result['objective']:.4f}")
    logger.info(f"p_ch={[round(x, 4) for x in result['p_ch']]}")
    logger.info(f"p_dis={[round(x, 4) for x in result['p_dis']]}")
    logger.info(f"soc={[round(x, 4) for x in result['soc']]}")
