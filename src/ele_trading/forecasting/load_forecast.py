"""Load forecasting (§15).

Implements load forecasting using AR + climatology pattern (similar to wind_forecast).
"""

from __future__ import annotations

from statistics import NormalDist
from typing import Mapping

import numpy as np
import pandas as pd

from ele_trading.forecasting.base import ForecastOutput
from ele_trading.forecasting.contracts import (
    ForecastRequest,
    ForecastResult,
    _prepare_history_series,
    _valid_time_index,
)


class LoadForecaster:
    """Load forecaster using AR(p) + climatology."""

    def __init__(self, ar_lags: int = 24, quantile_lower: float = 0.10, quantile_upper: float = 0.90):
        self.ar_lags = ar_lags
        self.quantile_lower = quantile_lower
        self.quantile_upper = quantile_upper
        self.ar_coef = None
        self.ar_intercept = 0.0
        self.climatology = None
        self.residual_std = None
        self._last_values: np.ndarray | None = None
        self._last_index: pd.DatetimeIndex | None = None
        self._history: pd.Series | None = None

    def fit(self, historical_load: pd.Series) -> "LoadForecaster":
        """Fit AR model and climatology."""
        # Climatology: average by hour-of-day and month
        df = pd.DataFrame({"load": historical_load})
        df["month"] = df.index.month
        df["hour"] = df.index.hour
        df["minute"] = df.index.minute
        self.climatology = df.groupby(
            ["month", "hour", "minute"]
        )["load"].mean()

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

        # Least squares fit with an intercept.
        X_aug = np.column_stack([np.ones(len(X)), X])
        coef = np.linalg.lstsq(X_aug, y, rcond=None)[0]
        self.ar_intercept = float(coef[0])
        self.ar_coef = coef[1:]

        # Residual std for quantile bands
        y_pred = X_aug @ coef
        residuals = y - y_pred
        self.residual_std = np.std(residuals)
        self._last_values = load_values[-self.ar_lags:].astype(float).copy()
        self._last_index = historical_load.index
        self._history = historical_load.astype(float).copy()

        return self

    def predict(
        self,
        horizon: int,
        start_time: pd.Timestamp | None = None,
        frequency: str | None = None,
    ) -> ForecastOutput:
        """Generate load forecast."""
        if self.ar_coef is None or self.climatology is None:
            raise ValueError("Model not fitted")

        # Simple approach: use climatology as base, AR for short-term adjustment
        if start_time is None:
            if self._last_index is not None:
                inferred = pd.infer_freq(self._last_index)
                offset = pd.tseries.frequencies.to_offset(
                    frequency or inferred or "h"
                )
                start_time = self._last_index[-1] + offset
            else:
                start_time = pd.Timestamp.now()

        # Generate timestamps
        freq = frequency or ("15min" if horizon > 24 else "h")
        timestamps = pd.date_range(start=start_time, periods=horizon, freq=freq)

        # Recursively blend the fitted AR state with the calendar baseline.
        last_values = self._last_values.copy()
        point_forecast: list[float] = []
        for ts in timestamps:
            key = (ts.month, ts.hour, ts.minute)
            if key in self.climatology.index:
                climatology_value = float(self.climatology.loc[key])
            else:
                climatology_value = float(self.climatology.mean())
            ar_value = float(
                self.ar_intercept + self.ar_coef @ last_values
            )
            predicted = max(
                0.0,
                0.7 * ar_value + 0.3 * climatology_value,
            )
            point_forecast.append(predicted)
            last_values = np.append(last_values[1:], predicted)
        point_array = np.asarray(point_forecast, dtype=float)

        # Quantile bands
        z_score = 1.28 if self.quantile_lower == 0.10 else 1.645  # 80% or 90% CI
        lower = point_array - z_score * self.residual_std
        upper = point_array + z_score * self.residual_std
        lower = np.maximum(lower, 0.0)  # load non-negative

        return ForecastOutput(
            horizon=horizon,
            point_forecast=point_array.tolist(),
            lower_quantile=lower.tolist(),
            upper_quantile=upper.tolist(),
        )


class LoadForecastModel:
    """Request-oriented load forecast model for all active scope levels."""

    _SCOPES = {"system", "region", "node", "portfolio", "site"}

    def __init__(
        self,
        *,
        history_by_scope: Mapping[tuple[str, str], pd.Series],
        ar_lags: int = 24,
    ) -> None:
        self.history_by_scope = dict(history_by_scope)
        self.ar_lags = ar_lags

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if request.target != "load":
            raise ValueError("load model requires target 'load'")
        if request.scope_type not in self._SCOPES:
            raise ValueError(
                "load scope_type must be system, region, node, portfolio, or site"
            )
        key = (request.scope_type, request.scope_id)
        history = self.history_by_scope.get(key)
        if history is None:
            raise ValueError(f"load history is missing for scope {key!r}")
        eligible = _prepare_history_series(
            history,
            issue_time=request.issue_time,
            frequency=request.frequency,
            field_name="load history",
        )

        valid_times = _valid_time_index(request)
        flags = [f"scope:{request.scope_type}"]
        if len(eligible) < self.ar_lags + 24:
            point = pd.Series(
                float(eligible.iloc[-1]),
                index=valid_times,
                dtype=float,
            )
            residual_scale = 0.0
            model_version = "load-persistence-v1"
            flags.append("degraded:insufficient_history")
        else:
            fitted = LoadForecaster(ar_lags=self.ar_lags).fit(eligible)
            output = fitted.predict(
                request.horizon,
                start_time=valid_times[0],
                frequency=request.frequency,
            )
            point = pd.Series(
                output.point_forecast,
                index=valid_times,
                dtype=float,
            )
            residual_scale = float(fitted.residual_std)
            model_version = "load-ar-climatology-v2"

        point_values = point.to_numpy(dtype=float)
        quantiles = {
            level: pd.Series(
                np.maximum(
                    0.0,
                    point_values
                    + NormalDist().inv_cdf(level) * residual_scale,
                ),
                index=valid_times,
                dtype=float,
            )
            for level in request.quantiles
        }
        return ForecastResult(
            request=request,
            point=point,
            quantiles=quantiles,
            unit="MW",
            model_version=model_version,
            feature_as_of=eligible.index.max(),
            quality_flags=tuple(flags),
        )


def bottom_up_reconcile(
    bottom_forecasts: Mapping[str, pd.Series],
    hierarchy: Mapping[str, tuple[str, ...]],
) -> dict[str, pd.Series]:
    """Build aggregate forecasts by recursively summing child forecasts."""
    reconciled = {
        name: values.copy()
        for name, values in bottom_forecasts.items()
    }

    def resolve(name: str, active: set[str]) -> pd.Series:
        if name in active:
            raise ValueError("load hierarchy contains a cycle")
        children = hierarchy.get(name)
        if children:
            child_values = [
                resolve(child, active | {name})
                for child in children
            ]
            index = child_values[0].index
            if any(
                not values.index.equals(index)
                for values in child_values[1:]
            ):
                raise ValueError("load hierarchy child indexes must align")
            reconciled[name] = sum(
                child_values[1:],
                child_values[0].copy(),
            )
            return reconciled[name]
        if name not in reconciled:
            raise ValueError(f"load hierarchy has no forecast for {name!r}")
        return reconciled[name]

    for aggregate in hierarchy:
        resolve(aggregate, set())
    return reconciled


def reconcile_hierarchy(
    base_forecasts: pd.DataFrame,
    summing_matrix: pd.DataFrame,
    *,
    method: str = "least_squares",
) -> pd.DataFrame:
    """Project independent forecasts onto the aggregate-consistent subspace."""
    if method not in {"least_squares", "constrained"}:
        raise ValueError(
            "reconciliation method must be least_squares or constrained"
        )
    if set(base_forecasts.columns) != set(summing_matrix.index):
        raise ValueError(
            "summing matrix rows must match base forecast columns"
        )
    ordered = summing_matrix.loc[base_forecasts.columns]
    matrix = ordered.to_numpy(dtype=float)
    reconciled_rows: list[np.ndarray] = []
    for values in base_forecasts.to_numpy(dtype=float):
        if method == "least_squares":
            bottom, *_ = np.linalg.lstsq(matrix, values, rcond=None)
        else:
            from scipy.optimize import lsq_linear

            bottom = lsq_linear(
                matrix,
                values,
                bounds=(0.0, np.inf),
            ).x
        reconciled_rows.append(matrix @ bottom)
    return pd.DataFrame(
        reconciled_rows,
        index=base_forecasts.index,
        columns=base_forecasts.columns,
        dtype=float,
    )
