"""Standalone demand-response economic assessment (not part of the main chain).

This module provides a simple post-hoc evaluation of DR participation
economics. The active trading chain uses ``solve_day_ahead_operational``
with ``dr_enabled=True`` for joint DR optimization instead. This tool
remains useful for quick standalone analysis.
"""

from __future__ import annotations

import numpy as np

from ele_trading.demand_response.contracts import DRDecision
from ele_trading.trading.contracts import MarketConfig


def estimate_arbitrage_opportunity_cost(
    p_net_plan: np.ndarray,
    realtime_price_forecast: np.ndarray,
    window: tuple[int, int],
    *,
    dt: float,
) -> float:
    """Return positive scheduled discharge value forgone in the DR window."""
    start, end = window
    plan = np.asarray(p_net_plan, dtype=float)
    price = np.asarray(realtime_price_forecast, dtype=float)
    if plan.shape != price.shape or plan.ndim != 1:
        raise ValueError("plan and price forecast must be aligned vectors")
    if not np.isfinite(plan).all() or not np.isfinite(price).all():
        raise ValueError("plan and price forecast must contain finite values")
    return float(
        np.sum(np.maximum(plan[start:end], 0.0) * price[start:end] * dt)
    )


def evaluate_dr_participation(
    adjustable_capacity: np.ndarray,
    config: MarketConfig,
    *,
    p_net_plan: np.ndarray,
    realtime_price_forecast: np.ndarray,
    expected_shortfall_mwh: float = 0.0,
) -> DRDecision:
    """Evaluate compensation minus opportunity, penalty and degradation costs."""
    adjustable = np.asarray(adjustable_capacity, dtype=float)
    if (
        adjustable.ndim != 1
        or not len(adjustable)
        or not np.isfinite(adjustable).all()
        or (adjustable < 0.0).any()
    ):
        raise ValueError(
            "adjustable_capacity must be a finite non-negative vector"
        )
    if (
        not np.isfinite(expected_shortfall_mwh)
        or expected_shortfall_mwh < 0.0
    ):
        raise ValueError("expected_shortfall_mwh must be finite and non-negative")
    window = (config.dr_window_start, config.dr_window_end)
    start, end = window
    if start < 0 or end > len(adjustable) or start >= end:
        raise ValueError("configured DR window must fit the supplied horizon")

    response_qty = float(
        np.sum(adjustable[start:end]) * config.dt
    )
    arbitrage_opportunity_cost = estimate_arbitrage_opportunity_cost(
        p_net_plan,
        realtime_price_forecast,
        window,
        dt=config.dt,
    )
    expected_compensation = (
        response_qty * config.dr_compensation_per_mwh
    )
    expected_penalty = (
        expected_shortfall_mwh * config.dr_penalty_per_mwh
    )
    degradation_cost = response_qty * config.deg_cost_per_mwh
    net_margin = (
        expected_compensation
        - arbitrage_opportunity_cost
        - expected_penalty
        - degradation_cost
    )
    meets_quantity = response_qty >= config.dr_minimum_response_mwh
    participate = (
        meets_quantity and net_margin > config.dr_minimum_margin
    )
    fulfill_risk = "elevated" if expected_shortfall_mwh > 0.0 else "low"
    reject_reason = None
    if not meets_quantity:
        reject_reason = (
            f"response quantity {response_qty:.3f} MWh is below configured "
            f"minimum {config.dr_minimum_response_mwh:.3f} MWh"
        )
    elif not participate:
        reject_reason = (
            f"net margin {net_margin:.2f} does not exceed configured "
            f"minimum {config.dr_minimum_margin:.2f}"
        )

    return DRDecision(
        participate=participate,
        response_qty=response_qty,
        window=window,
        expected_compensation=expected_compensation,
        arbitrage_opportunity_cost=arbitrage_opportunity_cost,
        expected_penalty=expected_penalty,
        degradation_cost=degradation_cost,
        net_margin=net_margin,
        fulfill_risk=fulfill_risk,
        reject_reason=reject_reason,
    )
