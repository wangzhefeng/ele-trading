from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from .base import ForecastOutput


class WindPowerForecaster:
    """Wind power forecaster with two backends.

    'physics'     – Wraps WindSimulator; takes NWP weather DataFrame.
    'statistical' – AR(p) model blended with hourly climatology.
    """

    def __init__(
        self,
        mode: Literal['physics', 'statistical'] = 'statistical',
        *,
        hub_height: float = 100.0,
        wind_shear_exp: float = 0.143,
        wind_speed_ref_height: float = 10.0,
        ar_lags: int = 24,
        persistence_weight_max: float = 0.8,
        quantile_lower: float = 0.10,
        quantile_upper: float = 0.90,
    ) -> None:
        if mode not in ('physics', 'statistical'):
            raise ValueError(f"mode must be 'physics' or 'statistical', got {mode!r}")
        self.mode = mode
        self.hub_height = hub_height
        self.wind_shear_exp = wind_shear_exp
        self.wind_speed_ref_height = wind_speed_ref_height
        self.ar_lags = ar_lags
        self.persistence_weight_max = persistence_weight_max
        self.quantile_lower = quantile_lower
        self.quantile_upper = quantile_upper

        self._ar_coef: np.ndarray | None = None
        self._ar_intercept: float = 0.0
        self._climatology: pd.Series | None = None
        self._capacity_mw: float = 1.0
        self._residual_lower: float = 0.0
        self._residual_upper: float = 0.0
        self._last_values: np.ndarray | None = None
        self._last_index: pd.DatetimeIndex | None = None

    def forecast_from_weather(
        self,
        weather_df: pd.DataFrame,
        capacity_mw: float,
        equiv_hours: float,
    ) -> ForecastOutput:
        """Physics-based wind forecast from NWP weather data.

        Args:
            weather_df: DatetimeIndex DataFrame with wind_speed (m/s),
                        temperature (°C), pressure (Pa).
            capacity_mw: Installed capacity (MW).
            equiv_hours: Annual equivalent full-load hours for calibration.
        """
        from ele_trading.resource_simulation.wind_simulation_v2 import WindSimulator
        sim = WindSimulator(
            hub_height=self.hub_height,
            wind_shear_exp=self.wind_shear_exp,
            wind_speed_ref_height=self.wind_speed_ref_height,
        )
        result = sim.simulate(weather_df, equiv_hours=equiv_hours, target_capacity_mw=capacity_mw)
        point = (result.power_series / 1000.0).tolist()  # kW → MW
        horizon = len(point)
        lower = [min(max(0.0, p * 0.75), capacity_mw) for p in point]
        upper = [min(capacity_mw, p * 1.25) for p in point]
        lower = [min(lo, hi) for lo, hi in zip(lower, upper)]
        return ForecastOutput(
            horizon=horizon,
            point_forecast=point,
            lower_quantile=lower,
            upper_quantile=upper,
        )

    def fit(self, historical_output: pd.Series) -> 'WindPowerForecaster':
        """Fit AR(p) + climatology model on historical hourly wind power output.

        Args:
            historical_output: Hourly pd.Series with DatetimeIndex.
        """
        if not isinstance(historical_output.index, pd.DatetimeIndex):
            raise TypeError('historical_output must have a DatetimeIndex')
        p = self.ar_lags
        if len(historical_output) < p + 24:
            raise ValueError(
                f'Need at least {p + 24} hours of history for AR({p}) fit, '
                f'got {len(historical_output)}'
            )

        y = historical_output.values.astype(float)
        self._capacity_mw = float(np.percentile(y, 99.5))

        n = len(y)
        X_ar = np.column_stack([y[i: n - p + i] for i in range(p)])
        X_aug = np.column_stack([np.ones(n - p), X_ar])
        y_target = y[p:]
        coef, _, _, _ = np.linalg.lstsq(X_aug, y_target, rcond=None)
        self._ar_intercept = float(coef[0])
        self._ar_coef = coef[1:]

        df = historical_output.rename('power').to_frame()
        df['month'] = df.index.month
        df['hour'] = df.index.hour
        self._climatology = df.groupby(['month', 'hour'])['power'].mean()

        y_hat = X_aug @ coef
        residuals = y_target - y_hat
        self._residual_lower = float(np.quantile(residuals, self.quantile_lower))
        self._residual_upper = float(np.quantile(residuals, self.quantile_upper))

        self._last_values = y[-p:].copy()
        self._last_index = historical_output.index
        return self

    def predict(self, horizon: int) -> ForecastOutput:
        """Generate wind power forecast.

        Args:
            horizon: Number of hours to forecast.
        """
        if self._ar_coef is None:
            raise RuntimeError('Call fit() before predict()')

        p = self.ar_lags
        last_vals = self._last_values.copy()

        next_time = (
            self._last_index[-1] + pd.Timedelta(hours=1)
            if self._last_index is not None
            else pd.Timestamp.now().floor('h')
        )
        forecast_times = pd.date_range(next_time, periods=horizon, freq='h')

        lambda_decay = np.log(20.0) / max(horizon, 48)

        point = np.zeros(horizon)
        for h in range(horizon):
            ar_pred = float(self._ar_intercept + self._ar_coef @ last_vals)
            ar_pred = float(np.clip(ar_pred, 0.0, self._capacity_mw))

            t = forecast_times[h]
            key = (t.month, t.hour)
            clim_pred = (
                float(self._climatology[key])
                if self._climatology is not None and key in self._climatology.index
                else float(np.mean(last_vals))
            )

            w = self.persistence_weight_max * np.exp(-lambda_decay * (h + 1))
            point[h] = float(np.clip(w * ar_pred + (1.0 - w) * clim_pred, 0.0, self._capacity_mw))
            last_vals = np.append(last_vals[1:], point[h])

        lower = np.minimum(np.clip(point + self._residual_lower, 0.0, self._capacity_mw), point)
        upper = np.maximum(np.clip(point + self._residual_upper, 0.0, self._capacity_mw), point)
        return ForecastOutput(
            horizon=horizon,
            point_forecast=point.tolist(),
            lower_quantile=lower.tolist(),
            upper_quantile=upper.tolist(),
        )
