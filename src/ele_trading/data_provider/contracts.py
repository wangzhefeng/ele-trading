"""Contracts owned by the market data-provider layer."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(slots=True)
class MarketDataSnapshot:
    """Versioned market data visible at one cutoff time.

    Rows are observations unless an ``is_observation`` column explicitly marks
    them otherwise. Future valid-time forecast rows are therefore allowed only
    when they carry ``is_observation=False``.
    """

    market: str
    scope_type: str
    scope_id: str
    as_of: pd.Timestamp
    frame: pd.DataFrame
    version: str
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.market, "market")
        _require_non_empty(self.scope_type, "scope_type")
        _require_non_empty(self.scope_id, "scope_id")
        _require_non_empty(self.version, "version")

        self.as_of = pd.Timestamp(self.as_of)
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if not isinstance(self.frame, pd.DataFrame):
            raise ValueError("frame must be a pandas DataFrame")
        if "timestamp" not in self.frame.columns:
            raise ValueError("frame must contain a timestamp column")

        timestamps = pd.DatetimeIndex(self.frame["timestamp"])
        if timestamps.tz is None:
            raise ValueError("timestamp data must be timezone-aware")
        if not timestamps.is_monotonic_increasing:
            raise ValueError("timestamp data must be monotonic; unordered rows found")
        if not timestamps.is_unique:
            raise ValueError("timestamp data must be unique; duplicate rows found")

        if "is_observation" not in self.frame.columns:
            raise ValueError("frame must contain an is_observation column")
        observation_mask = self.frame["is_observation"]
        if (
            observation_mask.isna().any()
            or not pd.api.types.is_bool_dtype(observation_mask.dtype)
        ):
            raise ValueError(
                "is_observation must be a non-null boolean column"
            )
        try:
            future_observations = observation_mask.to_numpy() & (
                timestamps > self.as_of
            )
        except TypeError as exc:
            raise ValueError(
                "timestamp data and as_of must use compatible timezones"
            ) from exc
        if future_observations.any():
            raise ValueError("observation rows cannot be newer than as_of")

        self.quality_flags = tuple(self.quality_flags)
