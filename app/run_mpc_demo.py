from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ele_trading.data.sample_data import load_default_intraday_prices, load_default_storage_config
from ele_trading.optimization.mpc_storage import run_storage_mpc


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
    print('=== 储能 MPC 滚动优化结果 ===')
    print(dispatch_df.to_string(index=False))
    print(f'累计窗口目标值={dispatch_df["step_objective"].sum():.4f}')
