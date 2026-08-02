"""Forecast request and result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd
from pandas.tseries.frequencies import to_offset

from ele_trading.domain.price_roles import PriceRole, normalize_price_role


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_finite_numeric(series: pd.Series, field_name: str) -> None:
    if not pd.api.types.is_numeric_dtype(series.dtype):
        raise ValueError(f"{field_name} must contain finite numeric values")
    if not np.isfinite(series.to_numpy(dtype=float)).all():
        raise ValueError(f"{field_name} must contain finite numeric values")


def _require_aware_timestamp(
    value: pd.Timestamp,
    field_name: str,
) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(
            f"{field_name} must be a valid timezone-aware timestamp"
        )
    return timestamp


def _require_forward_offset(
    frequency: str,
    issue_time: pd.Timestamp,
) -> pd.DateOffset:
    try:
        offset = to_offset(frequency)
        next_time = issue_time + offset
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "frequency must define a strictly forward offset"
        ) from exc
    if offset.n <= 0 or next_time <= issue_time:
        raise ValueError("frequency must define a strictly forward offset")
    return offset


def _valid_time_index(request: "ForecastRequest") -> pd.DatetimeIndex:
    offset = _require_forward_offset(
        request.frequency,
        request.issue_time,
    )
    valid_times: list[pd.Timestamp] = []
    valid_time = request.issue_time
    for _ in range(request.horizon):
        valid_time = valid_time + offset
        valid_times.append(valid_time)
    return pd.DatetimeIndex(valid_times)


def _prepare_history_series(
    history: pd.Series,
    *,
    issue_time: pd.Timestamp,
    frequency: str,
    field_name: str,
) -> pd.Series:
    """Validate one historical source and return observations usable as-of."""
    if not isinstance(history, pd.Series):
        raise ValueError(f"{field_name} must be a pandas Series")
    if (
        not isinstance(history.index, pd.DatetimeIndex)
        or history.index.tz is None
    ):
        raise ValueError(
            f"{field_name} must use a timezone-aware DatetimeIndex"
        )
    if history.index.has_duplicates:
        raise ValueError(f"{field_name} must not contain duplicate timestamps")
    if not history.index.is_monotonic_increasing:
        raise ValueError(
            f"{field_name} timestamps must be monotonic increasing"
        )
    _require_finite_numeric(history, field_name)

    if len(history) >= 2:
        expected_offset = _require_forward_offset(
            frequency,
            pd.Timestamp(issue_time),
        )
        matches_expected = all(
            previous + expected_offset == current
            for previous, current in zip(
                history.index[:-1],
                history.index[1:],
            )
        )
        if not matches_expected:
            inferred = (
                pd.infer_freq(history.index)
                if len(history) >= 3
                else None
            )
            if inferred is None:
                raise ValueError(
                    f"{field_name} time axis must be regular"
                )
            raise ValueError(
                f"{field_name} frequency must match request frequency "
                f"{frequency!r}"
            )

    eligible = history.loc[history.index <= issue_time].astype(float)
    if eligible.empty:
        raise ValueError(
            f"{field_name} has no values available by issue_time"
        )
    return eligible


@dataclass(slots=True)
class ForecastRequest:
    """Traceable request for one forecast target and scope."""

    target: str
    scope_type: str
    scope_id: str
    horizon: int
    frequency: str
    issue_time: pd.Timestamp
    quantiles: tuple[float, ...] = ()
    data: Mapping[str, object] = field(default_factory=dict)
    model_name: str = "default"
    model_version: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.target, "target")
        _require_non_empty(self.scope_type, "scope_type")
        _require_non_empty(self.scope_id, "scope_id")
        if not isinstance(self.horizon, int) or self.horizon <= 0:
            raise ValueError("horizon must be a positive integer")
        _require_non_empty(self.frequency, "frequency")

        self.issue_time = _require_aware_timestamp(
            self.issue_time,
            "issue_time",
        )
        _require_forward_offset(self.frequency, self.issue_time)
        self.quantiles = tuple(self.quantiles)
        if (
            any(not 0.0 < level < 1.0 for level in self.quantiles)
            or tuple(sorted(self.quantiles)) != self.quantiles
            or len(set(self.quantiles)) != len(self.quantiles)
        ):
            raise ValueError("quantiles must be ordered, unique, and within (0, 1)")
        if not isinstance(self.data, Mapping):
            raise ValueError("data must be a mapping")
        self.data = dict(self.data)
        if self.target == "price" and "price_role" in self.data:
            raw_price_role = self.data["price_role"]
            if not isinstance(raw_price_role, (PriceRole, str)):
                raise ValueError("price_role must be a string")
            self.data["price_role"] = normalize_price_role(raw_price_role).value
        _require_non_empty(self.model_name, "model_name")
        if self.model_version is not None:
            _require_non_empty(self.model_version, "model_version")


@dataclass(slots=True)
class ForecastResult:
    """Versioned forecast values aligned to one valid-time index."""

    request: ForecastRequest
    point: pd.Series
    quantiles: Mapping[float, pd.Series]
    unit: str
    model_version: str
    feature_as_of: pd.Timestamp
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.unit, "unit")
        _require_non_empty(self.model_version, "model_version")
        if not isinstance(self.point, pd.Series):
            raise ValueError("point must be a pandas Series")
        if len(self.point) != self.request.horizon:
            raise ValueError("point length must align with request horizon")
        _require_finite_numeric(self.point, "point")
        expected_index = _valid_time_index(self.request)
        if (
            not isinstance(self.point.index, pd.DatetimeIndex)
            or not self.point.index.equals(expected_index)
        ):
            raise ValueError(
                "point valid-time index must match the request horizon and frequency"
            )

        quantiles = dict(self.quantiles)
        if tuple(quantiles) != self.request.quantiles:
            raise ValueError("quantile levels must align with request quantiles")
        arrays: list[np.ndarray] = []
        for level, series in quantiles.items():
            if not isinstance(series, pd.Series):
                raise ValueError(f"quantile {level} must be a pandas Series")
            if len(series) != len(self.point) or not series.index.equals(self.point.index):
                raise ValueError(f"quantile {level} must align with point index and length")
            _require_finite_numeric(series, f"quantile {level}")
            arrays.append(series.to_numpy(dtype=float))

        if len(arrays) > 1 and (np.diff(np.vstack(arrays), axis=0) < 0).any():
            raise ValueError("quantile bands must be ordered at every valid time")

        self.quantiles = quantiles
        self.feature_as_of = _require_aware_timestamp(
            self.feature_as_of,
            "feature_as_of",
        )
        if self.feature_as_of > self.request.issue_time:
            raise ValueError("feature_as_of must not be later than issue_time")
        self.quality_flags = tuple(self.quality_flags)
