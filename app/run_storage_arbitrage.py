from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data.sample_data import load_default_day_ahead_prices, load_default_storage_config
from ele_trading.optimization.storage_arbitrage import solve_storage_arbitrage


if __name__ == '__main__':
    price_series = load_default_day_ahead_prices()
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
    print('=== 储能单市场套利结果 ===')
    print(f"objective={result['objective']:.4f}")
    print('p_ch=', [round(x, 4) for x in result['p_ch']])
    print('p_dis=', [round(x, 4) for x in result['p_dis']])
    print('soc=', [round(x, 4) for x in result['soc']])
