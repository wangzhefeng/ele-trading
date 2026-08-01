"""Demand-response product participation module.

Provides config-driven evaluation of DR participation economics,
separate from the single-settlement trading chain. Consumes
``MarketConfig`` (dr_* fields) from ``ele_trading.markets.single_settlement.contracts``.
"""

from ele_trading.demand_response.allocator import (
    estimate_arbitrage_opportunity_cost,
    evaluate_dr_participation,
)
from ele_trading.demand_response.contracts import DRDecision

__all__ = [
    "DRDecision",
    "estimate_arbitrage_opportunity_cost",
    "evaluate_dr_participation",
]
