from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests


def ensure_datetime_column(df: pd.DataFrame, time_col: str = "timestamp") -> pd.DataFrame:
    """确保气象数据包含可排序的时间列。"""

    result = df.copy()
    result[time_col] = pd.to_datetime(result[time_col])
    return result.sort_values(time_col).reset_index(drop=True)


def fetch_weather_open_meteo(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    hourly_fields: list[str] | None = None,
) -> pd.DataFrame:
    """从 Open-Meteo ERA5 接口获取小时级气象数据。

    返回字段固定包含 `timestamp`，其余字段由 `hourly_fields` 控制。
    该函数是 `investment_estimation` 的本地最小实现，避免资源仿真依赖主项目旧包路径。
    """

    fields = hourly_fields or ["wind_speed_100m", "temperature_2m"]
    url = "https://archive-api.open-meteo.com/v1/era5"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": fields,
        "timezone": "UTC",
    }
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    hourly = response.json()["hourly"]

    data = {"timestamp": pd.to_datetime(hourly["time"])}
    for field in fields:
        data[field] = hourly[field]
    return ensure_datetime_column(pd.DataFrame(data))


def load_weather_csv(path: str | Path, time_col: str = "timestamp") -> pd.DataFrame:
    """读取本地气象 CSV，并统一时间列名为 timestamp。"""

    df = pd.read_csv(path)
    return ensure_datetime_column(df.rename(columns={time_col: "timestamp"}))


def save_weather_csv(df: pd.DataFrame, path: str | Path) -> None:
    """保存气象数据 CSV。"""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False, encoding="utf-8")
