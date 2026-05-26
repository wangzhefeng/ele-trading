from __future__ import annotations

from typing import Iterable

import pandas as pd


def ensure_datetime_column(df: pd.DataFrame, time_col: str = "timestamp") -> pd.DataFrame:
    result = df.copy()
    result[time_col] = pd.to_datetime(result[time_col])
    return result.sort_values(time_col).reset_index(drop=True)


def ensure_unique_timestamps(df: pd.DataFrame, time_col: str = "timestamp") -> None:
    duplicated = df[time_col].duplicated()
    if duplicated.any():
        duplicated_values = df.loc[duplicated, time_col].astype(str).unique().tolist()
        raise ValueError(f"duplicate timestamps found: {duplicated_values[:5]}")


def resample_series_frame(
    df: pd.DataFrame,
    freq: str,
    time_col: str = "timestamp",
    numeric_cols: Iterable[str] | None = None,
) -> pd.DataFrame:
    result = ensure_datetime_column(df, time_col=time_col)
    result = result.set_index(time_col)
    if numeric_cols is None:
        numeric_cols = result.select_dtypes(include=["number", "bool"]).columns.tolist()
    full_index = pd.date_range(result.index.min(), result.index.max(), freq=freq)
    numeric_frame = result[list(numeric_cols)].reindex(full_index).interpolate(method="time").ffill().bfill()
    numeric_frame.index.name = time_col
    return numeric_frame.reset_index()


def align_series_on_timestamp(frames: list[pd.DataFrame], time_col: str = "timestamp") -> pd.DataFrame:
    if not frames:
        raise ValueError("frames must not be empty")
    normalized = [ensure_datetime_column(frame, time_col=time_col).set_index(time_col) for frame in frames]
    merged = pd.concat(normalized, axis=1).sort_index()
    merged.index.name = time_col
    return merged.reset_index()


def compute_quality_score(df: pd.DataFrame) -> pd.Series:
    penalties = (
        df.get("is_interpolated", False).astype(float) * 0.2
        + df.get("is_shifted_from_history", False).astype(float) * 0.3
        + df.get("is_filled_by_nearest_day", False).astype(float) * 0.3
    )
    return (1.0 - penalties).clip(lower=0.0, upper=1.0)
