"""operations — 资源运行层：日前运行计划与日内滚动控制。

职责：基于共享 BESS 物理内核的次日运行计划（``day_ahead_coupled``，
支持联合场景 CVaR 与 DR 两阶段联合优化）、冻结已执行前缀的日内滚动
重优化与失败回退（``intraday_rolling``）。
"""

from .day_ahead_coupled import solve_day_ahead_operational
from .intraday_rolling import solve_intraday_rolling

__all__ = [
    "solve_day_ahead_operational",
    "solve_intraday_rolling",
]
