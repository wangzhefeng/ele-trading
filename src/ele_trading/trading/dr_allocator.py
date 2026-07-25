"""Demand response allocator (§9)."""

from __future__ import annotations

import numpy as np

from ele_trading.trading.contracts import DRDecision, MarketConfig


def evaluate_dr_participation(
    adjustable_capacity: np.ndarray,
    dr_compensation: float,
    window: tuple[int, int],
    config: MarketConfig,
    margin: float = 0.0,
) -> DRDecision:
    """Evaluate whether to participate in demand response (§9).

    Compares DR compensation vs arbitrage opportunity cost.

    Args:
        adjustable_capacity: Available capacity for DR (MW) per period
        dr_compensation: DR compensation price (元/MWh)
        window: DR window (start, end) period indices
        config: MarketConfig
        margin: Participation margin threshold (元)

    Returns:
        DRDecision with participation recommendation
    """
    start, end = window
    window_capacity = adjustable_capacity[start:end]
    response_qty = float(np.sum(window_capacity) * 0.25)  # MWh (15min periods)

    # Estimate arbitrage opportunity cost (simplified)
    # In production, this would compare against optimal arbitrage schedule
    arbitrage_opportunity_cost = response_qty * 50.0  # rough estimate 50 元/MWh

    expected_compensation = response_qty * dr_compensation

    # Participation rule: compensation > opportunity cost + margin
    participate = expected_compensation > (arbitrage_opportunity_cost + margin)

    if participate:
        fulfill_risk = "low"  # simplified
        reject_reason = None
    else:
        fulfill_risk = "n/a"
        reject_reason = f"Compensation {expected_compensation:.0f} < opportunity cost {arbitrage_opportunity_cost:.0f} + margin {margin:.0f}"

    return DRDecision(
        participate=participate,
        response_qty=response_qty,
        window=window,
        expected_compensation=expected_compensation,
        arbitrage_opportunity_cost=arbitrage_opportunity_cost,
        fulfill_risk=fulfill_risk,
        reject_reason=reject_reason,
    )
