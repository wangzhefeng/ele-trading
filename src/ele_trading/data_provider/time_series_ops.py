"""Deprecated compatibility imports; use :mod:`ele_trading.data_provider.quality`."""

from .quality import (
    align_series_on_timestamp,
    compute_quality_score,
    detect_step_jumps,
    detect_zero_values,
    ensure_datetime_column,
    ensure_unique_timestamps,
    repair_anomalies,
    resample_series_frame,
)

__all__ = [
    "align_series_on_timestamp",
    "compute_quality_score",
    "detect_step_jumps",
    "detect_zero_values",
    "ensure_datetime_column",
    "ensure_unique_timestamps",
    "repair_anomalies",
    "resample_series_frame",
]
