"""Forecast evaluation metrics with explicit unit and grain metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True, slots=True)
class ForecastMetric:
    """One scalar forecast metric and its comparison semantics."""

    name: str
    value: float
    unit: str
    target_unit: str
    grain: str


def mean_absolute_error(
    actual: Iterable[float],
    predicted: Iterable[float],
    *,
    unit: str,
    grain: str,
) -> ForecastMetric:
    actual_values, predicted_values = _paired(actual, predicted)
    return _metric(
        "mae",
        float(np.mean(np.abs(actual_values - predicted_values))),
        unit,
        unit,
        grain,
    )


def root_mean_squared_error(
    actual: Iterable[float],
    predicted: Iterable[float],
    *,
    unit: str,
    grain: str,
) -> ForecastMetric:
    actual_values, predicted_values = _paired(actual, predicted)
    return _metric(
        "rmse",
        float(
            np.sqrt(
                np.mean((actual_values - predicted_values) ** 2)
            )
        ),
        unit,
        unit,
        grain,
    )


def pinball_loss(
    actual: Iterable[float],
    quantile_forecast: Iterable[float],
    *,
    quantile: float,
    unit: str,
    grain: str,
) -> ForecastMetric:
    if not 0.0 < quantile < 1.0:
        raise ValueError("quantile must be within (0, 1)")
    actual_values, forecast_values = _paired(
        actual,
        quantile_forecast,
    )
    error = actual_values - forecast_values
    loss = np.maximum(
        quantile * error,
        (quantile - 1.0) * error,
    )
    return _metric(
        "pinball_loss",
        float(np.mean(loss)),
        unit,
        unit,
        grain,
    )


def interval_coverage(
    actual: Iterable[float],
    lower: Iterable[float],
    upper: Iterable[float],
    *,
    unit: str,
    grain: str,
) -> ForecastMetric:
    actual_values = _values(actual)
    lower_values = _values(lower)
    upper_values = _values(upper)
    if not (
        len(actual_values)
        == len(lower_values)
        == len(upper_values)
        > 0
    ):
        raise ValueError("metric arrays must have the same non-zero length")
    if (lower_values > upper_values).any():
        raise ValueError("lower interval values must not exceed upper values")
    covered = (
        (actual_values >= lower_values)
        & (actual_values <= upper_values)
    )
    return _metric(
        "interval_coverage",
        float(np.mean(covered)),
        "ratio",
        unit,
        grain,
    )


def direction_accuracy(
    actual: Iterable[float],
    predicted: Iterable[float],
    *,
    unit: str,
    grain: str,
) -> ForecastMetric:
    actual_values, predicted_values = _paired(actual, predicted)
    if len(actual_values) < 2:
        raise ValueError(
            "direction accuracy requires at least two observations"
        )
    matches = (
        np.sign(np.diff(actual_values))
        == np.sign(np.diff(predicted_values))
    )
    return _metric(
        "direction_accuracy",
        float(np.mean(matches)),
        "ratio",
        unit,
        grain,
    )


def _paired(
    actual: Iterable[float],
    predicted: Iterable[float],
) -> tuple[np.ndarray, np.ndarray]:
    actual_values = _values(actual)
    predicted_values = _values(predicted)
    if len(actual_values) != len(predicted_values) or not len(actual_values):
        raise ValueError("metric arrays must have the same non-zero length")
    return actual_values, predicted_values


def _values(values: Iterable[float]) -> np.ndarray:
    array = np.asarray(list(values), dtype=float)
    if array.ndim != 1 or not np.isfinite(array).all():
        raise ValueError("metric arrays must contain finite one-dimensional values")
    return array


def _metric(
    name: str,
    value: float,
    metric_unit: str,
    target_unit: str,
    grain: str,
) -> ForecastMetric:
    if not target_unit.strip() or not grain.strip():
        raise ValueError("metric unit and grain must not be empty")
    return ForecastMetric(
        name=name,
        value=value,
        unit=metric_unit,
        target_unit=target_unit,
        grain=grain,
    )
