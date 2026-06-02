from __future__ import annotations

from datetime import timedelta
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


def detect_zero_values(
    series: pd.Series,
    threshold: float = 1.0,
) -> pd.Series:
    """检测零值和接近零的异常值。

    Parameters
    ----------
    series : Series
        待检测数值序列。
    threshold : float
        低于此值视为零值。

    Returns
    -------
    Series[bool]
        True 表示该位置为零值或 NaN。
    """
    return (series <= threshold) | series.isna()


def detect_step_jumps(
    series: pd.Series,
    n_sigma: float = 3.0,
) -> pd.Series:
    """检测阶跃跳变点。

    通过计算一阶差分的绝对值，超过 mean + n_sigma * std 的点判定为跳变。

    Parameters
    ----------
    series : Series
        待检测数值序列。
    n_sigma : float
        标准差倍数阈值。

    Returns
    -------
    Series[bool]
        True 表示该位置为阶跃跳变点。
    """
    diffs = series.diff().abs()
    threshold = diffs.mean() + n_sigma * diffs.std()
    return diffs > threshold


def repair_anomalies(
    df: pd.DataFrame,
    time_col: str,
    value_col: str,
    zero_threshold: float = 1.0,
    n_sigma: float = 3.0,
) -> pd.DataFrame:
    """检测并修复时序数据中的零值和阶跃跳变。

    修复策略：将异常位置设为 NaN，然后时间线性插值；
    仍为 NaN 的点用前日同时刻值补全，最后用均值兜底。

    Parameters
    ----------
    df : DataFrame
        输入数据。
    time_col : str
        时间列名。
    value_col : str
        数值列名。
    zero_threshold : float
        零值检测阈值。
    n_sigma : float
        阶跃检测标准差倍数。

    Returns
    -------
    DataFrame
        新增列: {value_col}_修正, 修正标识。
    """
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)

    corrected_col = f"{value_col}_修正"
    flag_col = "修正标识"

    df[corrected_col] = df[value_col]
    df[flag_col] = "正常"

    # 零值检测
    zero_mask = detect_zero_values(df[value_col], zero_threshold)
    df.loc[zero_mask, flag_col] = "0值待修正"

    # 阶跃检测
    jump_mask = detect_step_jumps(df[value_col], n_sigma)
    df.loc[jump_mask, flag_col] = "阶跃待修正"

    # 线性插值修复
    df.loc[df[flag_col].isin(["0值待修正", "阶跃待修正"]), corrected_col] = None
    df[corrected_col] = (
        df.set_index(time_col)[corrected_col]
        .interpolate(method="time", limit_direction="both")
        .reset_index(drop=True)
    )

    # 前日同刻值补全
    still_nan = df[corrected_col].isna()
    for i in df[still_nan].index:
        t_prev_day = df.loc[i, time_col] - timedelta(days=1)
        prev_day_val = df.loc[df[time_col] == t_prev_day, value_col]
        if not prev_day_val.empty:
            df.loc[i, corrected_col] = prev_day_val.values[0]
            df.loc[i, flag_col] = "前日值补全"
        else:
            df.loc[i, corrected_col] = df[value_col].mean()
            df.loc[i, flag_col] = "均值补全"

    # 更新标识
    df.loc[df[flag_col] == "0值待修正", flag_col] = "0值修正(线性)"
    df.loc[df[flag_col] == "阶跃待修正", flag_col] = "阶跃修正(线性)"

    return df
