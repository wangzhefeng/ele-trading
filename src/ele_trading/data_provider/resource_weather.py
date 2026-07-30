"""气象数据实现：Open-Meteo ERA5 抓取与气象 CSV IO。

本模块是实现本体（非弃用层）；``weather_data`` 为聚合入口，re-export 本模块。
"""

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
    """从 Open-Meteo 接口获取 ERA5-Land 小时级天气数据。

    主要用于风电建模输入（100 m 风速、2 m 气温）。

    Parameters
    ----------
    latitude, longitude : float
        测点经纬度（WGS84）。
    start_date, end_date : str
        起止日期，``YYYY-MM-DD``（闭区间）。
    hourly_fields : list[str]
        请求的小时级变量名，需为 Open-Meteo ERA5 支持的字段。

    Returns
    -------
    DataFrame
        列为 ``timestamp`` + 各 ``hourly_fields``；timestamp 为 UTC 无时区
        时间戳，按时间升序。
    """
    # ERA5 再分析归档接口（历史数据，非预报）
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

    # 返回帧：time → timestamp 列，其余字段原样展开
    data = {"timestamp": pd.to_datetime(hourly["time"])}
    for field in hourly_fields:
        data[field] = hourly[field]

    return ensure_datetime_column(pd.DataFrame(data))


def load_weather_csv(path: str | Path, time_col: str = "timestamp") -> pd.DataFrame:
    """读取气象 CSV，把时间列统一命名为 ``timestamp`` 并规整为升序。"""
    df = pd.read_csv(path)
    return ensure_datetime_column(df.rename(columns={time_col: "timestamp"}))


def save_weather_csv(df: pd.DataFrame, path: str | Path) -> None:
    """保存气象帧为 CSV（自动创建父目录，UTF-8 无索引）。"""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, index=False, encoding="utf-8")
