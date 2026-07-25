"""Mid-long-term position planning (§6.1)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ele_trading.trading.contracts import MarketConfig, PositionPlan


def plan_mid_long_position(
    q_load_forecast: pd.Series,  # monthly load forecast (MWh/month)
    p_long_forecast: pd.Series,  # monthly mid-long price forecast (元/MWh)
    p_spot_forecast: pd.Series,  # monthly spot price forecast (元/MWh)
    budget: float,
    config: MarketConfig,
    alpha_long_range: tuple[float, float] = (0.7, 0.9),
) -> PositionPlan:
    """Generate mid-long-term position plan.

    Determines optimal alpha_long (mid-long position ratio) based on price spread
    and risk budget.

    Args:
        q_load_forecast: Monthly load forecast
        p_long_forecast: Monthly mid-long price forecast
        p_spot_forecast: Monthly spot price forecast
        budget: Total budget (元)
        config: MarketConfig
        alpha_long_range: Allowed range for alpha_long

    Returns:
        PositionPlan with recommended alpha_long and monthly breakdown
    """
    # Simple heuristic: if mid-long price < spot price, favor higher alpha_long
    price_spread = p_spot_forecast - p_long_forecast
    spread_ratio = price_spread / p_spot_forecast

    # Map spread ratio to alpha_long within range
    # Positive spread (spot expensive) → higher alpha_long
    alpha_long = np.clip(
        alpha_long_range[0] + spread_ratio * 2,  # scale factor 2
        alpha_long_range[0],
        alpha_long_range[1],
    ).mean()

    alpha_dayah = (1 - alpha_long) * 0.6  # 60% of remainder to day-ahead
    alpha_real = 1 - alpha_long - alpha_dayah

    # Monthly breakdown
    q_long_monthly = q_load_forecast * alpha_long
    price_band = (
        float(p_long_forecast.min()),
        float(p_long_forecast.max()),
    )

    # Cost estimation
    expected_cost = float(
        np.sum(q_long_monthly * p_long_forecast)
        + np.sum(q_load_forecast * alpha_dayah * p_spot_forecast)
        + np.sum(q_load_forecast * alpha_real * p_spot_forecast)
    )
    budget_used = expected_cost / budget if budget > 0 else 0.0
    coverage = alpha_long + alpha_dayah  # fraction covered by forward contracts

    return PositionPlan(
        alpha_long=float(alpha_long),
        alpha_dayah=float(alpha_dayah),
        alpha_real=float(alpha_real),
        q_long_monthly=q_long_monthly,
        price_band=price_band,
        expected_cost=expected_cost,
        budget_used=budget_used,
        coverage=coverage,
    )
