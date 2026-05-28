from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass(slots=True)
class DemandConfig:
    """最大需量计算配置。"""

    window_minutes: int = 15
    """窗口时长（分钟），典型值: 15, 30."""

    window_type: Literal["fixed", "sliding"] = "sliding"
    """窗口类型: fixed=固定不重叠窗口, sliding=滑动窗口."""

    demand_price: float = 0.0
    """需量电价（元/kW/月）。"""

    power_unit: Literal["kW", "MW"] = "kW"
    """输入功率单位。"""


@dataclass(slots=True)
class DemandResult:
    """最大需量计算结果。"""

    max_demand: float
    """全局最大需量（kW）。"""

    peak_timestamp: pd.Timestamp
    """最大需量发生时刻。"""

    monthly_max: pd.Series
    """每月最大需量（index=Period, values=kW）。"""

    daily_max: pd.Series
    """每日最大需量（index=date, values=kW）。"""

    window_series: pd.Series
    """完整窗口平均功率序列（index=窗口起始时间, values=kW）。"""

    config: DemandConfig
    """计算所用配置。"""
