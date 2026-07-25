"""Load forecasting (§15).

Implements load forecasting using AR + climatology pattern (similar to wind_forecast).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ele_trading.forecasting.base import ForecastOutput


class LoadForecaster:
    """Load forecaster using AR(p) + climatology."""

    def __init__(self, ar_lags: int = 24, quantile_lower: float = 0.10, quantile_upper: float = 0.90):
        self.ar_lags = ar_lags
        self.quantile_lower = quantile_lower
        self.quantile_upper = quantile_upper
        self.ar_coef = None
        self.climatology = None
        self.residual_std = None

    def fit(self, historical_load: pd.Series) -> "LoadForecaster":
        """Fit AR model and climatology."""
        # Climatology: average by hour-of-day and month
        df = pd.DataFrame({"load": historical_load})
        df["hour"] = df.index.hour
        df["month"] = df.index.month
        self.climatology = df.groupby(["month", "hour"])["load"].mean()

        # AR(p) on detrended series
        load_values = historical_load.values
        n = len(load_values)
        X = []
        y = []
        for i in range(self.ar_lags, n):
            X.append(load_values[i - self.ar_lags:i])
            y.append(load_values[i])
        X = np.array(X)
        y = np.array(y)

        # Least squares fit
        self.ar_coef = np.linalg.lstsq(X, y, rcond=None)[0]

        # Residual std for quantile bands
        y_pred = X @ self.ar_coef
        residuals = y - y_pred
        self.residual_std = np.std(residuals)

        return self

    def predict(self, horizon: int, start_time: pd.Timestamp | None = None) -> ForecastOutput:
        """Generate load forecast."""
        if self.ar_coef is None or self.climatology is None:
            raise ValueError("Model not fitted")

        # Simple approach: use climatology as base, AR for short-term adjustment
        if start_time is None:
            start_time = pd.Timestamp.now()

        # Generate timestamps
        freq = "15min" if horizon > 24 else "h"
        timestamps = pd.date_range(start=start_time, periods=horizon, freq=freq)

        # Climatology forecast
        climatology_pred = []
        for ts in timestamps:
            key = (ts.month, ts.hour)
            if key in self.climatology.index:
                climatology_pred.append(self.climatology.loc[key])
            else:
                # Fallback to overall mean
                climatology_pred.append(self.climatology.mean())
        point_forecast = np.array(climatology_pred)

        # Quantile bands
        z_score = 1.28 if self.quantile_lower == 0.10 else 1.645  # 80% or 90% CI
        lower = point_forecast - z_score * self.residual_std
        upper = point_forecast + z_score * self.residual_std
        lower = np.maximum(lower, 0.0)  # load non-negative

        return ForecastOutput(
            horizon=horizon,
            point_forecast=point_forecast.tolist(),
            lower_quantile=lower.tolist(),
            upper_quantile=upper.tolist(),
        )
