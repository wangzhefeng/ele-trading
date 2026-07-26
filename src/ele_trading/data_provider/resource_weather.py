from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

from .quality import ensure_datetime_column


def fetch_weather_open_meteo(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    hourly_fields: list[str] =["wind_speed_100m", "temperature_2m"],
) -> pd.DataFrame:
    """
    Open-Meteo 接口获取 ERA5-Land 小时级天气数据，主要使用风速和气温作为风电建模输入
    返回列：Time, wind_speed_100m, temperature_2m

    TODO 补充注释
    Args:
        latitude (float): _description_
        longitude (float): _description_
        start_date (str): _description_
        end_date (str): _description_
        hourly_fields (list[str]): _description_

    Returns:
        pd.DataFrame: _description_
    """
    url = "https://archive-api.open-meteo.com/v1/era5"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": hourly_fields,
        "timezone": "UTC",
    }
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    hourly = response.json()["hourly"]

    data = {"timestamp": pd.to_datetime(hourly["time"])}
    for field in hourly_fields:
        data[field] = hourly[field]
    
    return ensure_datetime_column(pd.DataFrame(data))


def load_weather_csv(path: str | Path, time_col: str = "timestamp") -> pd.DataFrame:
    df = pd.read_csv(path)
    return ensure_datetime_column(df.rename(columns={time_col: "timestamp"}))


def save_weather_csv(df: pd.DataFrame, path: str | Path) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False, encoding="utf-8")
