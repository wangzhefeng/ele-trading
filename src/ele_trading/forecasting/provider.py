"""Forecast provider protocol (§15.2).

Defines the unified interface for all forecast providers (price, load, renewable).
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from ele_trading.trading.contracts import ForecastResult


class ForecastProvider(Protocol):
    """Unified forecast provider interface."""

    def get_price_forecast(
        self, market: str, horizon: int | str, *, quantiles: bool = False
    ) -> ForecastResult:
        """Get price forecast.

        Args:
            market: "dayah" | "real" | "long"
            horizon: Number of periods or "monthly"
            quantiles: Whether to return quantile bands

        Returns:
            ForecastResult with point forecast and optional quantiles
        """
        ...

    def get_load_forecast(
        self, scope: str, horizon: int | str, *, quantiles: bool = False
    ) -> ForecastResult:
        """Get load forecast.

        Args:
            scope: "dayah_open" | "real_open" | "long"
            horizon: Number of periods or "monthly"
            quantiles: Whether to return quantile bands

        Returns:
            ForecastResult with point forecast and optional quantiles
        """
        ...


class SimpleForecastProvider:
    """Simple implementation using existing forecasters."""

    def __init__(self, price_forecaster, load_forecaster, default_history_prices: list[float] | None = None):
        self.price_forecaster = price_forecaster
        self.load_forecaster = load_forecaster
        # Default history for SimplePriceForecaster (flat 300 元/MWh)
        self.default_history_prices = default_history_prices or [300.0] * 96

    def get_price_forecast(
        self, market: str, horizon: int | str, *, quantiles: bool = False
    ) -> ForecastResult:
        """Get price forecast using price_forecaster."""
        if isinstance(horizon, str):
            horizon = 12 if horizon == "monthly" else 96

        # SimplePriceForecaster needs history_prices, ARIMAForecaster only needs horizon
        try:
            output = self.price_forecaster.predict(self.default_history_prices, horizon)
        except TypeError:
            output = self.price_forecaster.predict(horizon)

        return ForecastResult(
            name=f"p_{market}_pre",
            unit="元/MWh",
            freq_minutes=15 if horizon > 24 else 0,
            issue_time=pd.Timestamp.now(),
            point=pd.Series(output.point_forecast),
            lower=pd.Series(output.lower_quantile) if quantiles else None,
            upper=pd.Series(output.upper_quantile) if quantiles else None,
            quantile_level=0.90 if quantiles else None,
        )

    def get_load_forecast(
        self, scope: str, horizon: int | str, *, quantiles: bool = False
    ) -> ForecastResult:
        """Get load forecast using load_forecaster."""
        if isinstance(horizon, str):
            horizon = 12 if horizon == "monthly" else 96

        output = self.load_forecaster.predict(horizon)
        return ForecastResult(
            name=f"Q_{scope}_pre",
            unit="MWh/刻",
            freq_minutes=15 if horizon > 24 else 0,
            issue_time=pd.Timestamp.now(),
            point=pd.Series(output.point_forecast),
            lower=pd.Series(output.lower_quantile) if quantiles else None,
            upper=pd.Series(output.upper_quantile) if quantiles else None,
            quantile_level=0.90 if quantiles else None,
        )
