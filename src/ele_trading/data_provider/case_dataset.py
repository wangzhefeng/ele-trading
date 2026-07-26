"""Deprecated trading-dataset import path.

Use :func:`ele_trading.data_provider.market_data.build_trading_case_dataset`.
Investment case construction lives only in ``data_provider.todo``.
"""

from .market_data import build_trading_case_dataset

__all__ = ["build_trading_case_dataset"]
