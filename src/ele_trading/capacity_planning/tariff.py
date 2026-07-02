"""容量规划投资测算的结构化电价合同。

把 `settle_monthly` 原来的 4 个标量电价升级为结构化 `Tariff`：电网购电价支持
逐时 TOU 曲线（尖/峰/平/谷），绿电结算价、需量电费、输配电价与政府基金作为
独立字段。市场分时规则由调用方/配置把时间戳映射成 `grid_buy_price_yuan_per_kwh`
数组后传入，本合同只消费数组，不内置省份规则。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class TouTier:
    """分时电价档位（仅用于描述/导出，不参与计费逻辑）。"""

    tier: str  # "sharp" | "peak" | "flat" | "valley" | "deep_valley"
    price_yuan_per_kwh: float


@dataclass(slots=True)
class DemandChargeConfig:
    """需量电费配置。

    收敛现有散落的 demand-charge 字段（`DistributedBESSDemandChargeConfig` 的
    mode/window 与 `demand_charge_rate`/`max_demand_price` 的费率）为统一合同。
    本轮实现 `point_max`（按月净负荷峰值），`sliding_window` 预留。
    """

    rate_yuan_per_kw: float = 0.0
    mode: str = "point_max"  # "point_max" | "sliding_window"
    window_minutes: int = 15


@dataclass(slots=True)
class Tariff:
    """结构化电价合同。

    `grid_buy_price_yuan_per_kwh` 为逐时步数组（TOU 曲线或扁平）；其余为标量。
    """

    timestamps: pd.DatetimeIndex
    grid_buy_price_yuan_per_kwh: np.ndarray
    green_price_yuan_per_kwh: float = 0.0
    demand_charge: DemandChargeConfig | None = None
    td_price_yuan_per_kwh: float = 0.0
    surcharges_yuan_per_kwh: float = 0.0

    @classmethod
    def from_flat(
        cls,
        timestamps,
        *,
        grid_buy_price: float,
        green_price: float = 0.0,
        demand_charge_rate: float = 0.0,
        td_price: float = 0.0,
        surcharges: float = 0.0,
    ) -> "Tariff":
        """由旧标量电价构造扁平 Tariff（每时步同价），作为向后兼容 adapter。"""
        n = len(timestamps)
        return cls(
            timestamps=pd.DatetimeIndex(timestamps),
            grid_buy_price_yuan_per_kwh=np.full(n, float(grid_buy_price), dtype=float),
            green_price_yuan_per_kwh=float(green_price),
            demand_charge=DemandChargeConfig(rate_yuan_per_kw=float(demand_charge_rate)),
            td_price_yuan_per_kwh=float(td_price),
            surcharges_yuan_per_kwh=float(surcharges),
        )

    def validate(self, length: int) -> None:
        """校验电价数组长度与非负性，尽早暴露错误配置。"""
        if len(self.grid_buy_price_yuan_per_kwh) != length:
            raise ValueError("grid_buy_price length must match dispatch horizon")
        if (np.asarray(self.grid_buy_price_yuan_per_kwh) < 0).any():
            raise ValueError("grid_buy_price must be non-negative")
