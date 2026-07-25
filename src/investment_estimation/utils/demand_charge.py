from __future__ import annotations

import pandas as pd


def monthly_peak_demand_cost(
    load: pd.Series,
    demand_price: float,
    *,
    freq: str = "ME",
) -> float:
    """按月最大需量计算需量电费。"""
    if not isinstance(load.index, pd.DatetimeIndex):
        raise ValueError("load must use a DatetimeIndex")
    if load.empty:
        return 0.0
    return float(load.resample(freq).max().sum() * demand_price)
