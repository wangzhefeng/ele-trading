# -*- coding: utf-8 -*-
"""数据清洗与重采样工具函数。

从 ba_eva_optim_version/ba_eva_2.py 提取并整合。
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd


def clean_and_merge_time(
    df: pd.DataFrame,
    date_col: str = "日期",
    hour_col: str = "小时",
    value_col: str = "电量",
    zero_threshold: float = 1.0,
) -> pd.DataFrame:
    """清洗时间序列数据：检测零值、阶跃，线性插值修复。

    Parameters
    ----------
    df : DataFrame
        原始数据，包含日期列、小时列和数值列。
    date_col : str
        日期列名。
    hour_col : str
        小时列名（如 "08:00", "24:00"）。
    value_col : str
        数值列名。
    zero_threshold : float
        低于此值视为零值待修正。

    Returns
    -------
    DataFrame
        新增列: 时间, {value_col}_修正, 修正标识。
    """
    df = df.copy()

    # 拼接时间列
    df["日期_clean"] = pd.to_datetime(df[date_col].astype(str).str.split(" ").str[0])
    df["小时_clean"] = df[hour_col].astype(str).str.strip()
    mask_24 = df["小时_clean"].str.startswith("24")
    df.loc[mask_24, "日期_clean"] += timedelta(days=1)
    df.loc[mask_24, "小时_clean"] = "00:00"
    df["时间"] = pd.to_datetime(df["日期_clean"].astype(str) + " " + df["小时_clean"])
    df = df.sort_values("时间").reset_index(drop=True)

    df[f"{value_col}_修正"] = df[value_col]
    df["修正标识"] = "正常"

    # 零值检测
    zero_mask = (df[value_col] <= zero_threshold) | (df[value_col].isna())
    df.loc[zero_mask, "修正标识"] = "0值待修正"

    # 阶跃检测
    diffs = df[value_col].diff().abs()
    threshold = diffs.mean() + 3 * diffs.std()
    jump_mask = diffs > threshold
    df.loc[jump_mask, "修正标识"] = "阶跃待修正"

    # 线性插值修复
    df[f"{value_col}_修正"] = df[value_col]
    df.loc[df["修正标识"].isin(["0值待修正", "阶跃待修正"]), f"{value_col}_修正"] = None
    df[f"{value_col}_修正"] = (
        df.set_index("时间")[f"{value_col}_修正"]
        .interpolate(method="time", limit_direction="both")
        .reset_index(drop=True)
    )

    # 无法插值的点用前一天同刻值补全
    still_nan = df[f"{value_col}_修正"].isna()
    for i in df[still_nan].index:
        t_prev_day = df.loc[i, "时间"] - timedelta(days=1)
        prev_day_val = df.loc[df["时间"] == t_prev_day, value_col]
        if not prev_day_val.empty:
            df.loc[i, f"{value_col}_修正"] = prev_day_val.values[0]
            df.loc[i, "修正标识"] = "前日值补全"
        else:
            df.loc[i, f"{value_col}_修正"] = df[value_col].mean()
            df.loc[i, "修正标识"] = "均值补全"

    # 更新标识
    df.loc[df["修正标识"] == "0值待修正", "修正标识"] = "0值修正(线性)"
    df.loc[df["修正标识"] == "阶跃待修正", "修正标识"] = "阶跃修正(线性)"

    # 清理临时列
    df = df.drop(columns=["日期_clean", "小时_clean"], errors="ignore")

    return df


def resample_to_15min(
    df: pd.DataFrame,
    time_col: str = "Time",
    cols: list[str] | None = None,
) -> pd.DataFrame:
    """将 1 小时分辨率数据线性插值为 15 分钟。

    Parameters
    ----------
    df : DataFrame
        输入数据。
    time_col : str
        时间列名。
    cols : list[str] | None
        需要插值的列名列表。为 None 时自动选择所有数值列。

    Returns
    -------
    DataFrame
        15 分钟分辨率的数据。
    """
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
    df = df.dropna(subset=[time_col])
    df = df.sort_values(time_col)
    df = df.drop_duplicates(subset=[time_col], keep="last")
    df = df.set_index(time_col)

    if cols is None:
        cols = [c for c in df.columns if df[c].dtype != "O"]

    df_resampled = df[cols].resample("15min").interpolate("linear")
    df_resampled = df_resampled.reset_index()

    return df_resampled
