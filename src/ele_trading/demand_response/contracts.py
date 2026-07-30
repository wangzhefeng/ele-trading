"""Demand-response module contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DRDecision:
    """Demand-response product participation decision."""

    participate: bool
    response_qty: float
    window: tuple[int, int]
    expected_compensation: float
    arbitrage_opportunity_cost: float
    expected_penalty: float
    degradation_cost: float
    net_margin: float
    fulfill_risk: str
    reject_reason: str | None = None
