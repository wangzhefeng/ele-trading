from __future__ import annotations

from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd


def infer_dt_hours(t) -> float:
    t = pd.Series(pd.to_datetime(t), name="Time").sort_values().reset_index(drop=True)
    if len(t) < 2:
        raise ValueError("时间点数量不足，无法推断 dt")
    dt = t.diff().dropna().mode().iloc[0]
    return float(dt.total_seconds() / 3600.0)


def monthly_kwh(time_index, kw_arr: np.ndarray, dt_hours: float) -> pd.Series:
    idx = pd.DatetimeIndex(pd.to_datetime(time_index).to_numpy())
    s = pd.Series(np.asarray(kw_arr, dtype="float64"), index=idx)
    return (s.groupby(s.index.to_period("M")).sum() * dt_hours).sort_index()


def generate_time_points(start_time, end_time, step: timedelta) -> list[datetime]:
    """Generate left-closed, right-open time points."""
    if step <= timedelta(0):
        raise ValueError("step must be positive")

    start = _to_datetime(start_time)
    end = _to_datetime(end_time)
    time_points = []
    current = start
    while current < end:
        time_points.append(current)
        current += step
    return time_points


def generate_days(start_time, end_time) -> list[datetime]:
    return generate_time_points(start_time, end_time, timedelta(days=1))


def generate_hours(start_time, end_time) -> list[datetime]:
    return generate_time_points(start_time, end_time, timedelta(hours=1))


def generate_quarters(start_time, end_time) -> list[datetime]:
    return generate_time_points(start_time, end_time, timedelta(minutes=15))


def generate_5mins(start_time, end_time) -> list[datetime]:
    return generate_time_points(start_time, end_time, timedelta(minutes=5))


def end_of_that_day(current_day_time) -> datetime:
    current = _to_datetime(current_day_time)
    return datetime.combine((current + timedelta(days=1)).date(), time.min)


def start_of_this_es_cycle(current_time, division_hour: int) -> datetime:
    _validate_division_hour(division_hour)
    current = _to_datetime(current_time)
    division_time = datetime.combine(
        current.date(), datetime.min.time().replace(hour=division_hour)
    )
    if current < division_time:
        return division_time - timedelta(days=1)
    return division_time


def end_of_this_es_cycle(current_time, division_hour: int) -> datetime:
    _validate_division_hour(division_hour)
    current = _to_datetime(current_time)
    division_time = datetime.combine(
        current.date(), datetime.min.time().replace(hour=division_hour)
    )
    if current >= division_time:
        return division_time + timedelta(days=1)
    return division_time


def es_cycle_window(current_time, division_hour: int) -> tuple[datetime, datetime]:
    return (
        start_of_this_es_cycle(current_time, division_hour),
        end_of_this_es_cycle(current_time, division_hour),
    )


def process_time_index(
    raw_df: pd.DataFrame,
    column_name: str,
    new_column_name: str = "time",
    *,
    keep: str = "last",
    sort_index: bool = True,
) -> pd.DataFrame:
    """Convert a timestamp column into a deduplicated datetime index."""
    if column_name not in raw_df.columns:
        raise ValueError(f"missing time column: {column_name}")

    df = raw_df.copy(deep=True)
    df[new_column_name] = pd.to_datetime(df[column_name])
    df.drop_duplicates(subset=new_column_name, keep=keep, inplace=True, ignore_index=True)
    df.set_index(new_column_name, inplace=True)
    if sort_index:
        df.sort_index(inplace=True)
    return df


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return pd.to_datetime(value).to_pydatetime()


def _validate_division_hour(division_hour: int) -> None:
    if not 0 <= division_hour <= 23:
        raise ValueError("division_hour must be between 0 and 23")
