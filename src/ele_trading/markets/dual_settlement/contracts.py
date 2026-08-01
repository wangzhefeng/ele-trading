"""双结算（偏差带考核）模式的市场配置与结算报告契约。

配置字段仅覆盖结算引擎所需（偏差带、中长期回收、结算时段）；
v1 归档中的报量报价/风控/策略权重字段不随本插件移植（参与者角色差异，
待报价契约设计时另行实现）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MarketConfig:
    """双结算（偏差带考核）模式市场规则配置（字段与 YAML 一一对应）。"""

    settlement_mode: str = "band_deviation"
    settle_periods: int = 96

    # 日前偏差考核带
    lam_l: float = 0.95
    lam_u: float = 1.05

    # 中长期月度回收带与倍率
    lam_l_long: float = 0.90
    lam_u_long: float = 1.05
    m_long: float = 1.2
    cpen_long_applies_to_storage: bool = True


@dataclass(slots=True)
class SettlementReport:
    """双结算日结算报告：量价结算 + 日前偏差考核 + 中长期回收。"""

    c_daily: float
    cpen_dayah: float
    cpen_long: float
    cost_daily: float
    cost_baseline: float
    delta_cost: float
