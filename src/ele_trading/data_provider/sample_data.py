from __future__ import annotations

from pathlib import Path

from .loader import load_price_scenarios, load_price_series, load_bess_config


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = PACKAGE_ROOT / 'data'


def get_sample_paths() -> dict[str, Path]:
    """返回项目内置样例数据路径。"""
    return {
        'day_ahead_prices': DATA_ROOT / 'raw' / 'sample_day_ahead_prices.csv',
        'intraday_prices': DATA_ROOT / 'raw' / 'sample_intraday_prices.csv',
        'bess_config': DATA_ROOT / 'raw' / 'sample_bess_config.yaml',
        'price_scenarios': DATA_ROOT / 'scenarios' / 'sample_price_scenarios.csv',
    }


def load_default_day_ahead_prices():
    paths = get_sample_paths()
    return load_price_series(paths['day_ahead_prices'], time_col='hour', price_col='price', label='day_ahead')


def load_default_intraday_prices():
    paths = get_sample_paths()
    return load_price_series(paths['intraday_prices'], time_col='step', price_col='price', label='intraday')


def load_default_bess_config():
    paths = get_sample_paths()
    return load_bess_config(paths['bess_config'])


def load_default_price_scenarios():
    paths = get_sample_paths()
    return load_price_scenarios(paths['price_scenarios'])
