from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

from .base import ForecastOutput


class SolarPowerForecaster:
    """Solar power forecaster with two backends.

    'physics'  – Wraps SolarSimulator; takes NWP weather DataFrame as input.
    'harmonic' – Fits Fourier harmonic regression on historical hourly output.
    """

    def __init__(
        self,
        mode: Literal['physics', 'harmonic'] = 'harmonic',
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        timezone: str = 'Asia/Shanghai',
        tilt: float | None = None,
        azimuth: float = 180.0,
        altitude: float = 0.0,
        n_harmonics_daily: int = 4,
        n_harmonics_annual: int = 2,
        quantile_lower: float = 0.10,
        quantile_upper: float = 0.90,
    ) -> None:
        if mode not in ('physics', 'harmonic'):
            raise ValueError(f"mode must be 'physics' or 'harmonic', got {mode!r}")
        if mode == 'physics' and (latitude is None or longitude is None):
            raise ValueError("latitude and longitude required for physics mode")
        self.mode = mode
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = timezone
        self.tilt = tilt
        self.azimuth = azimuth
        self.altitude = altitude
        self.n_harmonics_daily = n_harmonics_daily
        self.n_harmonics_annual = n_harmonics_annual
        self.quantile_lower = quantile_lower
        self.quantile_upper = quantile_upper

        self._coef: np.ndarray | None = None
        self._capacity_mw: float | None = None
        self._residual_lower: float = 0.0
        self._residual_upper: float = 0.0
        self._last_index: pd.DatetimeIndex | None = None

    def forecast_from_weather(
        self,
        weather_df: pd.DataFrame,
        capacity_mw: float,
        equiv_hours: float,
    ) -> ForecastOutput:
        """Physics-based solar forecast from NWP weather data.

        Args:
            weather_df: DatetimeIndex DataFrame with ghi (W/m²), temp_air (°C),
                        wind_speed (m/s).
            capacity_mw: Installed capacity (MW).
            equiv_hours: Annual equivalent full-load hours for calibration.
        """
        from ele_trading.capacity_planning.solar_simulation import SolarSimulator
        tilt = self.tilt if self.tilt is not None else self.latitude * 0.9
        sim = SolarSimulator(
            latitude=self.latitude,
            longitude=self.longitude,
            timezone=self.timezone,
            tilt=tilt,
            azimuth=self.azimuth,
            altitude=self.altitude,
        )
        result = sim.simulate(weather_df, equiv_hours=equiv_hours, target_capacity_mw=capacity_mw)
        point = result.output_mw.tolist()
        horizon = len(point)
        lower = [min(max(0.0, p * 0.85), capacity_mw) for p in point]
        upper = [min(capacity_mw, p * 1.15) for p in point]
        lower = [min(lo, hi) for lo, hi in zip(lower, upper)]
        return ForecastOutput(
            horizon=horizon,
            point_forecast=point,
            lower_quantile=lower,
            upper_quantile=upper,
        )

    def fit(self, historical_output: pd.Series) -> 'SolarPowerForecaster':
        """Fit Fourier harmonic model on historical hourly solar power output.

        Args:
            historical_output: Hourly pd.Series with DatetimeIndex.
        """
        if not isinstance(historical_output.index, pd.DatetimeIndex):
            raise TypeError('historical_output must have a DatetimeIndex')
        if len(historical_output) < 48:
            raise ValueError('Need at least 48 hours of history to fit')

        y = historical_output.values.astype(float)
        X = self._build_features(historical_output.index)
        self._coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        self._capacity_mw = float(np.percentile(y, 99.5))
        self._last_index = historical_output.index

        residuals = y - X @ self._coef
        self._residual_lower = float(np.quantile(residuals, self.quantile_lower))
        self._residual_upper = float(np.quantile(residuals, self.quantile_upper))
        return self

    def predict(
        self,
        horizon: int,
        start_time: pd.Timestamp | None = None,
    ) -> ForecastOutput:
        """Generate solar power forecast.

        Args:
            horizon: Number of hours to forecast.
            start_time: First timestamp; defaults to one hour after last training point.
        """
        if self._coef is None:
            raise RuntimeError('Call fit() before predict()')
        if start_time is None:
            if self._last_index is None:
                raise ValueError('start_time required')
            start_time = self._last_index[-1] + pd.Timedelta(hours=1)

        if self._last_index is not None and self._last_index.tz is not None:
            if getattr(start_time, 'tzinfo', None) is None:
                start_time = start_time.tz_localize(self._last_index.tz)
        idx = pd.date_range(start_time, periods=horizon, freq='h')
        X_pred = self._build_features(idx)
        point = np.clip(X_pred @ self._coef, 0.0, self._capacity_mw)
        lower = np.clip(point + self._residual_lower, 0.0, self._capacity_mw)
        upper = np.clip(point + self._residual_upper, 0.0, self._capacity_mw)
        lower = np.minimum(lower, point)
        upper = np.maximum(upper, point)
        return ForecastOutput(
            horizon=horizon,
            point_forecast=point.tolist(),
            lower_quantile=lower.tolist(),
            upper_quantile=upper.tolist(),
        )

    def _build_features(self, index: pd.DatetimeIndex) -> np.ndarray:
        hour = index.hour + index.minute / 60.0
        day = index.dayofyear.astype(float)
        cols: list[np.ndarray] = [np.ones(len(index))]
        for k in range(1, self.n_harmonics_daily + 1):
            cols.append(np.sin(2 * np.pi * k * hour / 24))
            cols.append(np.cos(2 * np.pi * k * hour / 24))
        for j in range(1, self.n_harmonics_annual + 1):
            cols.append(np.sin(2 * np.pi * j * day / 365))
            cols.append(np.cos(2 * np.pi * j * day / 365))
        return np.column_stack(cols)
