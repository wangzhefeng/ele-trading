"""内置最小样例（prices/config）的路径与快捷加载入口。

与 ``ele_trading.trading.sample_data`` 区分：后者负责蒙西 30 天日清分样例
（daily_sample_*.csv）的 provider 与 fixture 生成；本模块只管理
data/trading/ 下 prices/config 两个最小样例。
"""

from __future__ import annotations

from pathlib import Path

from .asset_data import load_bess_config
from .market_data import load_price_series

# 项目根目录（src/ele_trading/data_provider/ → 上三级）与 data/ 根
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PACKAGE_ROOT / 'data'
CONFIGS_ROOT = PACKAGE_ROOT / 'configs'


def get_sample_paths() -> dict[str, Path]:
    """返回项目内置样例数据路径（24 点日前价 / 日内价 / BESS 配置）。

    价格样例在 ``data/trading/prices/``，BESS 配置在 ``configs/optimization/bess.yaml``。
    """
    return {
        'day_ahead_prices': DATA_ROOT / 'trading' / 'prices' / 'sample_day_ahead_prices.csv',
        'intraday_prices': DATA_ROOT / 'trading' / 'prices' / 'sample_intraday_prices.csv',
        'bess_config': CONFIGS_ROOT / 'optimization' / 'bess.yaml',
    }


def load_default_day_ahead_prices():
    """加载 24 点日前价格样例（hour,price）为 ``PriceSeries``。"""
    paths = get_sample_paths()
    return load_price_series(paths['day_ahead_prices'], time_col='hour', price_col='price', label='day_ahead')


def load_default_intraday_prices():
    """加载日内价格样例（step,price）为 ``PriceSeries``。"""
    paths = get_sample_paths()
    return load_price_series(paths['intraday_prices'], time_col='step', price_col='price', label='intraday')


def load_default_bess_config():
    """加载储能最小样例配置为 ``BESSConfig``。"""
    paths = get_sample_paths()
    return load_bess_config(paths['bess_config'])
