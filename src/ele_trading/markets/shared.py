"""markets 共享工具：跨结算模式通用的结算辅助函数。

当前收录 ``aggregate_to_settle_periods``（结算时段能量守恒聚合）。
统一采用单结算插件的现役实现（含 ndim 校验）；归档 v1 双结算版本
（带 settle_periods == n 快速路径）语义等价，已随归档删除。
"""

from __future__ import annotations

import numpy as np


def aggregate_to_settle_periods(
    quantity: np.ndarray,
    settle_periods: int,
) -> np.ndarray:
    """Aggregate interval energy while preserving the total quantity."""
    values = np.asarray(quantity, dtype=float)
    if (
        settle_periods <= 0
        or values.ndim != 1
        or len(values) % settle_periods != 0
    ):
        raise ValueError(
            "settle_periods must be a positive divisor of the horizon"
        )
    return values.reshape(settle_periods, -1).sum(axis=1)
