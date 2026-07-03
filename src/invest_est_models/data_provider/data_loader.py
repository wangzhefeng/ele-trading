from __future__ import annotations

from pathlib import Path

import pandas as pd

from .price_type import normalize_price_type


def read_load_csv(path: str | Path) -> pd.DataFrame:
    """读取负荷 CSV，并把通用 value 字段规范为 load_kw。"""

    df = _read_time_csv(path, required=("time", "value"))
    return df.rename(columns={"value": "load_kw"})


def read_price_csv(path: str | Path) -> pd.DataFrame:
    """读取分时电价 CSV，并把中文或英文 price_type 统一为英文编码。"""

    df = _read_time_csv(path, required=("time", "price", "price_type"))
    df["price_type"] = df["price_type"].map(normalize_price_type)
    return df


def read_resource_csv(path: str | Path) -> pd.DataFrame:
    """读取已仿真的风光资源 CSV，缺少单类资源时按 0 处理。"""

    df = _read_time_csv(path, required=("time",))
    # 允许单风、单光场景复用同一接口；缺省资源列不视为错误。
    for col in ("pv_kw", "wind_kw"):
        if col not in df.columns:
            df[col] = 0.0
    return df[["time", "pv_kw", "wind_kw"]]


def build_timeseries(load: pd.DataFrame, price: pd.DataFrame, resource: pd.DataFrame) -> pd.DataFrame:
    """按时间戳对齐负荷、电价、资源三类输入，形成仿真主表。"""

    # 当前 MVP 要求三类输入在 time 上精确重合；缺口会在 inner join 后丢弃。
    df = load.merge(price, on="time", how="inner").merge(resource, on="time", how="inner")
    if df.empty:
        raise ValueError("No overlapping timestamps across load, price, and resource data.")
    df = df.sort_values("time").reset_index(drop=True)
    # dt_hours 是后续 kW -> kWh、储能功率约束和月度结算的统一时间尺度。
    df["dt_hours"] = infer_dt_hours(df["time"])
    validate_timeseries(df)
    return df


def validate_timeseries(df: pd.DataFrame) -> None:
    """校验对齐后的时序主表，发现关键数据质量问题时抛出异常。"""

    required = {"time", "load_kw", "price", "price_type", "pv_kw", "wind_kw", "dt_hours"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing timeseries columns: {sorted(missing)}")
    if df["time"].duplicated().any():
        raise ValueError("Duplicate timestamps found in timeseries data.")
    if df[["time", "load_kw", "price", "price_type", "pv_kw", "wind_kw", "dt_hours"]].isna().any().any():
        raise ValueError("Missing values found in timeseries data.")
    if (df["dt_hours"] <= 0).any():
        raise ValueError("dt_hours must be positive.")
    if (df["load_kw"] < 0).any():
        raise ValueError("load_kw must be non-negative.")
    if (df["price"] < 0).any():
        raise ValueError("price must be non-negative.")
    if (df[["pv_kw", "wind_kw"]] < 0).any().any():
        raise ValueError("pv_kw and wind_kw must be non-negative.")


def infer_dt_hours(time: pd.Series) -> pd.Series:
    """根据相邻时间戳推断每个时间步长度，单位小时。"""

    if len(time) < 2:
        raise ValueError("At least two timestamps are required to infer interval length.")
    delta = time.shift(-1) - time
    # 最后一行没有下一个时间戳，沿用倒数第二个间隔，适配等间隔全年序列。
    delta.iloc[-1] = delta.iloc[-2]
    hours = delta.dt.total_seconds() / 3600.0
    if (hours <= 0).any():
        raise ValueError("Timestamps must be strictly increasing.")
    return hours


def _read_time_csv(path: str | Path, required: tuple[str, ...]) -> pd.DataFrame:
    """读取 CSV、校验必需字段，并统一解析 time 列。"""

    df = pd.read_csv(path)
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    return df
