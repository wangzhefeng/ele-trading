"""Explicit demo-only seasonal-naive forecasts from prior observations."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ele_trading.forecasting.contracts import (
    ForecastRequest,
    ForecastResult,
)
from ele_trading.forecasting.price_history import resolve_price_history


class SeasonalNaiveTradingForecastProvider:
    """Forecast one day from an earlier fully observed daily profile."""

    def __init__(
        self,
        history: pd.DataFrame,
        *,
        feature_as_of: pd.Timestamp,
    ) -> None:
        self.history = history.copy()
        self.feature_as_of = pd.Timestamp(feature_as_of)
        if self.feature_as_of.tzinfo is None:
            raise ValueError("feature_as_of must be timezone-aware")

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if self.feature_as_of > request.issue_time:
            raise ValueError(
                "seasonal-naive history is newer than request issue_time"
            )
        price_role = None
        if request.target == "price":
            price_role, history_series = resolve_price_history(
                self.history,
                request,
            )
            history_values = history_series.to_numpy(dtype=float)
        elif request.target == "load":
            history_values = self.history["Q_real_load"].to_numpy(dtype=float)
        else:
            history_values = None
        if history_values is None:
            base = np.zeros(request.horizon, dtype=float)
        else:
            repeats = int(np.ceil(request.horizon / len(history_values)))
            base = np.tile(history_values, repeats)[: request.horizon]
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        point = pd.Series(base, index=index)
        scale = 0.10 if request.target == "price" else 0.05
        if request.target == "price":
            spread = np.maximum(np.abs(base) * scale, 1.0)
            lower = base - spread
            upper = base + spread
        else:
            lower = np.maximum(base * (1.0 - scale), 0.0)
            upper = base * (1.0 + scale)
        quantile_values = {
            request.quantiles[0]: pd.Series(lower, index=index),
            request.quantiles[1]: pd.Series(upper, index=index),
        }
        return ForecastResult(
            request=request,
            point=point,
            quantiles=quantile_values,
            unit=(
                "CNY/MWh"
                if request.target == "price"
                else "MWh/period"
            ),
            model_version="seasonal-naive-demo-v1",
            feature_as_of=self.feature_as_of,
            quality_flags=(
                "demo:prior-day-observation",
                *(
                    (f"price_role:{price_role.value}",)
                    if price_role is not None
                    else ()
                ),
            ),
        )
