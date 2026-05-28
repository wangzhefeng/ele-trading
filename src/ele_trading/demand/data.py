from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ele_trading.data_provider.time_series_ops import (
    ensure_datetime_column,
    ensure_unique_timestamps,
)


def load_load_curve(path: str | Path) -> pd.DataFrame:
    """从 CSV 文件加载负荷曲线。

    CSV 文件须包含 ``timestamp`` 和 ``power_kw`` 两列。

    Parameters
    ----------
    path : str or Path
        CSV 文件路径。

    Returns
    -------
    DataFrame
        含 timestamp (datetime64) 和 power_kw (float64) 列，按时间排序。
    """
    df = pd.read_csv(path)
    if "timestamp" not in df.columns or "power_kw" not in df.columns:
        raise ValueError(f"CSV 须包含 timestamp 和 power_kw 列，实际列: {list(df.columns)}")
    df = ensure_datetime_column(df, time_col="timestamp")
    ensure_unique_timestamps(df, time_col="timestamp")
    df["power_kw"] = df["power_kw"].astype(float)
    return df


def generate_simulated_load(
    n_days: int = 30,
    freq: str = "15min",
    seed: int = 42,
) -> pd.DataFrame:
    """生成模拟负荷曲线数据。

    负荷形状: 基础负荷 + 双峰日周期（上午10点、下午14点）+ 随机噪声。

    Parameters
    ----------
    n_days : int
        模拟天数。
    freq : str
        采样频率，默认 "15min"。
    seed : int
        随机种子。

    Returns
    -------
    DataFrame
        含 timestamp 和 power_kw 列。
    """
    rng = np.random.default_rng(seed)
    n_points = int(n_days * 24 * 60 / int(freq.replace("min", "")))
    timestamps = pd.date_range("2024-01-01", periods=n_points, freq=freq)

    hours = timestamps.hour + timestamps.minute / 60.0
    # 基础负荷 500 kW
    base = 500.0
    # 上午峰 ~10:00
    peak_am = 300.0 * np.exp(-0.5 * ((hours - 10.0) / 1.5) ** 2)
    # 下午峰 ~14:00
    peak_pm = 400.0 * np.exp(-0.5 * ((hours - 14.0) / 2.0) ** 2)
    # 夜间低谷
    night_dip = -150.0 * np.exp(-0.5 * ((hours - 3.0) / 2.0) ** 2)
    # 随机噪声
    noise = rng.normal(0, 30, n_points)

    power_kw = base + peak_am + peak_pm + night_dip + noise
    power_kw = np.maximum(power_kw, 0.0)

    return pd.DataFrame({"timestamp": timestamps, "power_kw": power_kw})
