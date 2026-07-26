"""Mid-long position planning for the single-settlement market."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ele_trading.trading.contracts import MarketConfig, PositionPlan


def plan_mid_long_position(
    q_load_forecast: pd.Series,  # monthly load forecast (MWh/month)
    p_long_forecast: pd.Series,  # monthly mid-long price forecast (元/MWh)
    p_spot_forecast: pd.Series,  # monthly real-time price forecast (元/MWh)
    budget: float,
    config: MarketConfig,
    alpha_long_range: tuple[float, float] = (0.7, 0.9),
) -> PositionPlan:
    """Return long-contract coverage and residual real-time exposure."""
    if not (
        q_load_forecast.index.equals(p_long_forecast.index)
        and q_load_forecast.index.equals(p_spot_forecast.index)
    ):
        raise ValueError("monthly forecasts must use the same index")
    if budget < 0.0 or not np.isfinite(budget):
        raise ValueError("budget must be finite and non-negative")

    price_spread = p_spot_forecast - p_long_forecast
    spread_ratio = price_spread / p_spot_forecast.replace(0.0, np.nan)
    spread_ratio = spread_ratio.fillna(0.0)

    # Map spread ratio to alpha_long within range
    # Positive spread (spot expensive) → higher alpha_long
    alpha_long = np.clip(
        alpha_long_range[0] + spread_ratio * 2,  # scale factor 2
        alpha_long_range[0],
        alpha_long_range[1],
    ).mean()

    alpha_real = 1.0 - alpha_long

    # Monthly breakdown
    q_long_monthly = q_load_forecast * alpha_long
    price_band = (
        float(p_long_forecast.min()),
        float(p_long_forecast.max()),
    )

    residual_real = q_load_forecast - q_long_monthly
    monthly_cost = (
        q_long_monthly * p_long_forecast
        + residual_real * p_spot_forecast
    )
    expected_cost = float(monthly_cost.sum())
    expected_risk = float(monthly_cost.std(ddof=0))
    budget_used = expected_cost / budget if budget > 0 else 0.0
    coverage = float(alpha_long)

    return PositionPlan(
        alpha_long=float(alpha_long),
        alpha_real=float(alpha_real),
        q_long_monthly=q_long_monthly,
        price_band=price_band,
        expected_cost=expected_cost,
        expected_risk=expected_risk,
        budget_used=budget_used,
        coverage=coverage,
    )
