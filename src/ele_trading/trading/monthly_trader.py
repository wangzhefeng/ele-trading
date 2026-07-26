"""Monthly trading: centralized bidding ladder and position rebalancing (§6.2, §6.3)."""

from __future__ import annotations

import numpy as np

from ele_trading.trading.contracts import (
    BidLadder,
    CorridorAdvice,
    MarketConfig,
)


def build_position_corridor(
    *,
    position_gap: float,
    tolerance: float,
    price_band: tuple[float, float],
    config: MarketConfig,
) -> CorridorAdvice:
    """Expose a quantity/price corridor when no orderbook is available."""
    values = np.asarray(
        [position_gap, tolerance, *price_band],
        dtype=float,
    )
    if not np.isfinite(values).all() or tolerance < 0.0:
        raise ValueError("corridor inputs must be finite with tolerance >= 0")
    if price_band[0] > price_band[1]:
        raise ValueError("price_band lower bound must not exceed upper bound")
    direction = "buy" if position_gap < 0.0 else "sell"
    absolute_gap = abs(position_gap)
    quantity_range = (
        max(0.0, absolute_gap - tolerance),
        absolute_gap + tolerance,
    )
    clipped_price_range = (
        float(
            np.clip(
                price_band[0],
                config.monthly_price_floor,
                config.monthly_price_cap,
            )
        ),
        float(
            np.clip(
                price_band[1],
                config.monthly_price_floor,
                config.monthly_price_cap,
            )
        ),
    )
    return CorridorAdvice(
        direction=direction,
        qty_range=quantity_range,
        price_range=clipped_price_range,
        reason=(
            "orderbook unavailable; expose configured position and price "
            "corridor without fabricating counterparties"
        ),
    )


def build_bid_ladder(
    q_low: float,
    q_high: float,
    p_low: float,
    p_high: float,
    k: int,
    direction: str,
    config: MarketConfig,
    clear_prob_model: str = "uniform",
) -> BidLadder:
    """Generate centralized bidding ladder (§6.2).

    Args:
        q_low: Lower bound of target position band
        q_high: Upper bound of target position band
        p_low: Lower bound of acceptable price band
        p_high: Upper bound of acceptable price band
        k: Number of segments
        direction: "buy" or "sell"
        config: MarketConfig
        clear_prob_model: Clearing probability model ("uniform" or "linear")

    Returns:
        BidLadder with cumulative quantities and prices
    """
    delta_q = (q_high - q_low) / k
    delta_p = (p_high - p_low) / k

    bid_qty = []
    bid_price = []
    clear_prob = []

    for i in range(1, k + 1):
        # Cumulative quantity
        qty = q_low + i * delta_q
        bid_qty.append(qty)

        # Price depends on direction
        if direction == "buy":
            # Buy ladder: price decreases with quantity (willing to pay more for first units)
            price = p_high - (i - 1) * delta_p
        elif direction == "sell":
            # Sell ladder: price increases with quantity (willing to accept less for first units)
            price = p_low + (i - 1) * delta_p
        else:
            raise ValueError(f"Unknown direction: {direction}")

        bid_price.append(price)

        # Clearing probability (simplified model)
        if clear_prob_model == "uniform":
            prob = 1.0 / k
        elif clear_prob_model == "linear":
            # Higher probability for more competitive prices
            if direction == "buy":
                prob = 1.0 - (i - 1) / k  # higher price → higher prob
            else:
                prob = i / k  # lower price → higher prob
        else:
            prob = 0.5
        clear_prob.append(prob)

    # Clip prices to market limits
    bid_price = [
        np.clip(
            p,
            config.monthly_price_floor,
            config.monthly_price_cap,
        )
        for p in bid_price
    ]

    # Expected cost/revenue
    expected_cost = sum(q * p * prob for q, p, prob in zip(bid_qty, bid_price, clear_prob))
    expected_revenue = expected_cost if direction == "sell" else 0.0

    return BidLadder(
        direction=direction,
        bid_qty=bid_qty,
        bid_price=bid_price,
        clear_prob=clear_prob,
        expected_cost=expected_cost,
        expected_revenue=expected_revenue,
    )


def rebalance_position_gap(
    gap: np.ndarray,
    pos_tol: float,
    config: MarketConfig,
) -> dict:
    """Generate position rebalancing advice (§6.3).

    Args:
        gap: Position gap array (Q_long_held - Q_need)
        pos_tol: Position tolerance band
        config: MarketConfig

    Returns:
        Dict with rebalancing advice per period
    """
    advice = []
    for t, g in enumerate(gap):
        if abs(g) <= pos_tol:
            action = "hold"
            reason = f"Gap {g:.2f} within tolerance ±{pos_tol:.2f}"
            priority = 0
        elif g < -pos_tol:
            action = "buy"
            reason = f"Gap {g:.2f} below tolerance → buy to cover shortage"
            priority = int(abs(g) / pos_tol)  # higher gap → higher priority
        else:
            action = "sell"
            reason = f"Gap {g:.2f} above tolerance → sell to reduce surplus"
            priority = int(abs(g) / pos_tol)

        advice.append({
            "period": t,
            "gap": g,
            "action": action,
            "priority": priority,
            "reason": reason,
        })

    return {
        "advice": advice,
        "total_buy": -sum(a["gap"] for a in advice if a["action"] == "buy"),
        "total_sell": sum(a["gap"] for a in advice if a["action"] == "sell"),
        "num_adjustments": sum(1 for a in advice if a["action"] != "hold"),
    }
