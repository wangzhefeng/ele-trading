"""气象数据聚合入口（facade）。

自身不含任何实现，仅把两个实现模块的能力 re-export 为统一入口：
- ``resource_weather``：Open-Meteo ERA5 抓取 + 气象 CSV IO；
- ``weather_io``：Mongo/NetCDF/气象模拟器/实测文件夹读取。
"""

# --- Open-Meteo 抓取与 CSV IO（实现：resource_weather） ---
from .resource_weather import (
    fetch_weather_open_meteo,
    load_weather_csv,
    save_weather_csv,
)

# --- Mongo / NetCDF / 模拟器 / 实测文件（实现：weather_io） ---
from .weather_io import (
    DEFAULT_LAG,
    DEFAULT_LATS,
    DEFAULT_LONS,
    DEFAULT_QUERY_LIMIT,
    WEATHER_VARS,
    NetCDFToJSON,
    WeatherMongoClient,
    WeatherMongoReader,
    WeatherSimulator,
    get_real_for_points,
    make_sample_load_data,
    make_sample_weather_dataset,
    read_measured_folder,
)

__all__ = [
    "DEFAULT_LAG",
    "DEFAULT_LATS",
    "DEFAULT_LONS",
    "DEFAULT_QUERY_LIMIT",
    "WEATHER_VARS",
    "NetCDFToJSON",
    "WeatherMongoClient",
    "WeatherMongoReader",
    "WeatherSimulator",
    "fetch_weather_open_meteo",
    "get_real_for_points",
    "load_weather_csv",
    "make_sample_load_data",
    "make_sample_weather_dataset",
    "read_measured_folder",
    "save_weather_csv",
]
