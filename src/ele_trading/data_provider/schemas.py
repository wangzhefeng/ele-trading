"""活动数据类型定义。

注意与 ``contracts.MarketDataSnapshot`` 的层次差异：本模块是轻量数据结构
（价格序列、实测功率序列），``MarketDataSnapshot`` 才是带版本/防前瞻校验的
完整交易快照。
"""

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd


@dataclass(slots=True)
class PriceSeries:
    """价格序列数据结构（整数索引，供 24 点/日内 step 样例使用）。

    ``timestamps`` 为整数时刻索引（hour 或 step），不是真实时间戳；
    真实时间轴场景应使用 ``MarketDataSnapshot``。
    """

    timestamps: List[int]
    prices: List[float]
    label: str = "sample"


@dataclass(slots=True)
class ObservedPowerSeries:
    """带时区的实测负荷/新能源功率序列（无投资语义）。

    构造时强制校验：索引必须带时区、单调递增、无重复；数值必须为有限数。
    """

    values: pd.Series                            # 功率值，DatetimeIndex
    unit: str                                    # 单位（如 "kW"/"MW"）
    source: str                                  # 数据来源（溯源用，通常为文件路径）
    quality_flags: tuple[str, ...] = ()          # 质量标记

    def __post_init__(self) -> None:
        # --- 本体必须是 pandas Series ---
        if not isinstance(self.values, pd.Series):
            raise ValueError("values must be a pandas Series")

        # --- 索引校验：DatetimeIndex + 时区 + 单调 + 唯一 ---
        index = self.values.index
        if (
            not isinstance(index, pd.DatetimeIndex)
            or index.tz is None
            or not index.is_monotonic_increasing
            or not index.is_unique
        ):
            raise ValueError(
                "observed power index must be timezone-aware, monotonic, and unique"
            )

        # --- 数值校验：数值型且全部有限（拒绝 NaN/inf） ---
        if (
            not pd.api.types.is_numeric_dtype(self.values.dtype)
            or not np.isfinite(self.values.to_numpy(dtype=float)).all()
        ):
            raise ValueError("observed power values must be finite numeric values")

        # --- 溯源字段非空 ---
        if not self.unit.strip():
            raise ValueError("unit must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")

        # 统一为 tuple，保证不可变
        self.quality_flags = tuple(self.quality_flags)
