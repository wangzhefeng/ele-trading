"""Positions contracts：中长期/月度头寸决策的公开契约。

迁移自原 ``trading/contracts.py``（纯移动，定义逐行不变）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class PositionPlan:
    """Mid-long position result without a financial day-ahead position."""

    alpha_long: float
    alpha_real: float
    q_long_monthly: pd.Series
    price_band: tuple[float, float]
    expected_cost: float
    expected_risk: float
    budget_used: float
    coverage: float


@dataclass(slots=True)
class BidLadder:
    """Monthly centralized-market order ladder."""

    direction: str
    bid_qty: list[float]
    bid_price: list[float]
    clear_prob: list[float]
    expected_cost: float
    expected_revenue: float


@dataclass(slots=True)
class CorridorAdvice:
    """Transparent fallback when orderbook data is unavailable."""

    direction: str
    qty_range: tuple[float, float]
    price_range: tuple[float, float]
    reason: str
