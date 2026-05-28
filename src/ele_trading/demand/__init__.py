"""最大需量计算模块。

计算最大需量电功率，支持固定窗口和滑动窗口两种方式。
"""
from __future__ import annotations

from .calc import calc_demand, calc_demand_charge, calc_fixed_window, calc_sliding_window
from .config import DemandConfig, DemandResult
from .data import generate_simulated_load, load_load_curve
from .plot import plot_load_with_demand, plot_monthly_demand

__all__ = [
    "DemandConfig",
    "DemandResult",
    "calc_demand",
    "calc_demand_charge",
    "calc_fixed_window",
    "calc_sliding_window",
    "load_load_curve",
    "generate_simulated_load",
    "plot_load_with_demand",
    "plot_monthly_demand",
]
