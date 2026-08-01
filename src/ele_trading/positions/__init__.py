"""positions — 中长期/月度头寸决策层。

职责：中长期覆盖结构与实时敞口（``mid_long_planner``）、月度集中竞价
阶梯申报与缺口再平衡（``monthly_trader``）。市场规则参数经
``markets.single_settlement.MarketConfig`` 注入，不硬编码。
"""

from .contracts import BidLadder, CorridorAdvice, PositionPlan
from .mid_long_planner import plan_mid_long_position
from .monthly_trader import (
    build_bid_ladder,
    build_position_corridor,
    rebalance_position_gap,
)

__all__ = [
    "BidLadder",
    "CorridorAdvice",
    "PositionPlan",
    "build_bid_ladder",
    "build_position_corridor",
    "plan_mid_long_position",
    "rebalance_position_gap",
]
