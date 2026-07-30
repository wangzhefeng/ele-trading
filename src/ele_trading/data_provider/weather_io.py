"""气象数据实现：Mongo / NetCDF / 模拟器 / 实测文件读取。

结构分五节：
1. 模拟数据生成（合成气象场 + 合成负荷）；
2. NetCDF 解析（``NetCDFToJSON``）；
3. MongoDB 客户端（``WeatherMongoClient`` / ``WeatherMongoReader``）；
4. 离线模拟器（``WeatherSimulator``，接口与 Mongo 读取器对齐，可无库运行）；
5. 批量抓取与实测文件读取。

xarray / pymongo 为可选依赖：未安装时对应功能在调用处显式抛 ImportError，
不影响本模块其他部分的导入与使用。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 可选依赖：xarray（NetCDF 处理）
try:
    import xarray as xr

    HAS_XARRAY = True
except ImportError:
    HAS_XARRAY = False

# 可选依赖：pymongo（MongoDB 接入）
try:
    from pymongo import MongoClient
    from urllib.parse import quote_plus

    HAS_PYMONGO = True
except ImportError:
    HAS_PYMONGO = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 模块常量
# ---------------------------------------------------------------------------

# 气象变量清单：平均气温 / 相对湿度 / 太阳辐射 / 风向 / 风速
WEATHER_VARS = ["t_avg", "rh", "ssrd", "wd", "ws"]
DEFAULT_LAG = 6                   # 默认滞后阶数（特征工程用）
DEFAULT_QUERY_LIMIT = 5000        # 单次 Mongo 查询默认返回上限

# 内蒙古区域默认格点范围
DEFAULT_LATS = [round(x, 1) for x in np.arange(37.0, 44.0, 0.5)]
DEFAULT_LONS = [round(x, 1) for x in np.arange(105.0, 121.0, 0.5)]


# ===========================================================================
# Section 1: 模拟数据生成
# ===========================================================================

def make_sample_weather_dataset(
    n_times: int = 168,
    lats: Optional[List[float]] = None,
    lons: Optional[List[float]] = None,
    seed: int = 42,
) -> "xr.Dataset":
    """
    生成模拟 NetCDF 天气数据集，覆盖内蒙古地区格点。

    变量：t_avg(°C), rh(%), ssrd(W/m²), ws(m/s), wd(°), pre(mm)
    包含 time × lat × lon 三个维度。

    Parameters
    ----------
    n_times : int
        时间步数，默认 168（7天逐小时）
    lats : list of float
        纬度列表，默认 37.0~43.5，步长 0.5
    lons : list of float
        经度列表，默认 105.0~120.5，步长 0.5
    seed : int
        随机种子，保证可复现
    """
    if not HAS_XARRAY:
        raise ImportError("xarray 未安装，无法生成模拟数据集。请运行: pip install xarray")

    if lats is None:
        lats = DEFAULT_LATS
    if lons is None:
        lons = DEFAULT_LONS

    rng = np.random.default_rng(seed)
    n_lat = len(lats)
    n_lon = len(lons)

    # 时间轴：从 2024-01-01 00:00 开始
    start = np.datetime64("2024-01-01T00:00")
    times = start + np.arange(n_times).astype("timedelta64[h]")

    hours_of_day = np.arange(n_times) % 24

    # --- 温度 t_avg ---
    # 纬度越高温越低，日周期正弦
    lat_grid = np.array(lats).reshape(-1, 1, 1)  # (n_lat, 1, 1)
    hour_grid = hours_of_day.reshape(1, -1, 1)  # (1, n_times, 1)
    lon_grid = np.array(lons).reshape(1, 1, -1)  # (1, 1, n_lon)

    base_temp = 15.0 - (lat_grid - 37.0) * 2.0  # 纬度梯度
    t_avg = (
        base_temp
        + 5.0 * np.sin(2 * np.pi * (hour_grid - 6) / 24)  # 日周期
        + rng.normal(0, 1.5, (n_lat, n_times, n_lon))  # 随机扰动
    )

    # --- 湿度 rh ---
    # 与温度反相关，日周期相反
    rh = (
        55.0
        - (t_avg - base_temp) * 2.0
        + 10.0 * np.sin(2 * np.pi * (hour_grid + 4) / 24)
        + rng.normal(0, 5.0, (n_lat, n_times, n_lon))
    )
    rh = np.clip(rh, 10, 100)

    # --- 太阳辐射 ssrd ---
    # 仅在日出到日落期间有值（6:00-18:00）
    day_mask = (hours_of_day >= 6) & (hours_of_day <= 18)
    ssrd = np.zeros((n_lat, n_times, n_lon))
    peak_hour = 12
    for h in range(n_times):
        if hours_of_day[h] >= 6 and hours_of_day[h] <= 18:
            # 日照角按 6→18 点正弦包络，正午 12 点达到峰值
            angle = np.pi * (hours_of_day[h] - 6) / 12
            ssrd[:, h, :] = (
                600.0 * np.sin(angle) + rng.normal(0, 30, (n_lat, n_lon))
            )
    ssrd = np.clip(ssrd, 0, None)

    # --- 风速 ws ---
    ws = (
        3.0
        + 2.0 * np.sin(2 * np.pi * (hour_grid - 8) / 24)
        + rng.exponential(2.0, (n_lat, n_times, n_lon))
    )
    ws = np.clip(ws, 0.1, 25.0)

    # --- 风向 wd ---
    wd_base = rng.uniform(0, 360, (n_lat, 1, n_lon))
    wd_noise = np.cumsum(rng.normal(0, 15, (n_lat, n_times, n_lon)), axis=1)
    wd = (wd_base + wd_noise) % 360

    # --- 降水 pre ---
    pre = np.zeros((n_lat, n_times, n_lon))
    rain_mask = rng.random((n_lat, n_times, n_lon)) < 0.05  # 5% 概率降雨
    pre[rain_mask] = rng.exponential(2.0, np.sum(rain_mask))
    pre = np.round(pre, 1)

    # 内部计算形状为 (n_lat, n_times, n_lon)，输出转为 xarray 约定的
    # (time, lat, lon) 维序
    ds = xr.Dataset(
        {
            "t_avg": (["time", "lat", "lon"], t_avg.transpose(1, 0, 2)),
            "rh": (["time", "lat", "lon"], rh.transpose(1, 0, 2)),
            "ssrd": (["time", "lat", "lon"], ssrd.transpose(1, 0, 2)),
            "ws": (["time", "lat", "lon"], ws.transpose(1, 0, 2)),
            "wd": (["time", "lat", "lon"], wd.transpose(1, 0, 2)),
            "pre": (["time", "lat", "lon"], pre.transpose(1, 0, 2)),
        },
        coords={
            "time": times,
            "lat": lats,
            "lon": lons,
        },
    )

    # 设置变量单位
    ds["t_avg"].attrs["units"] = "°C"
    ds["rh"].attrs["units"] = "%"
    ds["ssrd"].attrs["units"] = "W/m²"
    ds["ws"].attrs["units"] = "m/s"
    ds["wd"].attrs["units"] = "°"
    ds["pre"].attrs["units"] = "mm"

    return ds


def make_sample_load_data(
    n_times: int = 168,
    seed: int = 42,
) -> pd.DataFrame:
    """
    生成模拟电力负荷数据，模拟内蒙古地区统调负荷。

    特征：日双峰（上午 ~10h、晚间 ~20h），周末略低，叠加随机噪声。

    Returns
    -------
    pd.DataFrame
        列: ['datetime', 'value']，value 单位 MW
    """
    rng = np.random.default_rng(seed)
    start = np.datetime64("2024-01-01T00:00")
    times = pd.date_range(start=start, periods=n_times, freq="h")
    hours = times.hour
    # 周几（0=周一, 6=周日）
    weekdays = times.dayofweek

    # 基础负荷 10000 MW + 日双峰
    load = np.full(n_times, 10000.0)

    # 上午高峰 9:00-11:00（高斯包络，中心 10 点）
    morning_peak = 2000 * np.exp(-((hours - 10) ** 2) / 8)
    # 晚间高峰 19:00-21:00（高斯包络，中心 20 点）
    evening_peak = 2500 * np.exp(-((hours - 20) ** 2) / 8)
    # 午间低谷 13:00-15:00（高斯包络，中心 14 点）
    afternoon_valley = -800 * np.exp(-((hours - 14) ** 2) / 6)

    load += morning_peak + evening_peak + afternoon_valley

    # 周末效应：负荷整体下浮 10%
    weekend_mask = weekdays >= 5
    load[weekend_mask] *= 0.90

    # 随机噪声（2%）
    load += rng.normal(0, 200, n_times)

    return pd.DataFrame({"datetime": times, "value": load})


# ===========================================================================
# Section 2: NetCDF 解析
# ===========================================================================

class NetCDFToJSON:
    """将 NetCDF 文件或 xarray.Dataset 解析为多变量 JSON。"""

    def __init__(
        self,
        file_path: Optional[str] = None,
        dataset: Optional["xr.Dataset"] = None,
    ):
        """
        Parameters
        ----------
        file_path : str, optional
            NetCDF 文件路径
        dataset : xr.Dataset, optional
            直接传入 xarray Dataset，优先级高于 file_path
        """
        if not HAS_XARRAY:
            raise ImportError("xarray 未安装，请运行: pip install xarray")

        # dataset 直传优先；否则从文件打开；两者都缺则报错
        if dataset is not None:
            self.ds = dataset
        elif file_path is not None:
            self.ds = xr.open_dataset(file_path)
        else:
            raise ValueError("必须提供 file_path 或 dataset 参数")

    def extract_units(self) -> Dict[str, Optional[str]]:
        """提取所有变量的单位（无 units 属性的变量值为 None）。"""
        units: Dict[str, Optional[str]] = {}
        for name, var in self.ds.variables.items():
            units[name] = var.attrs.get("units", None)
        return units

    def to_dataframe(self) -> pd.DataFrame:
        """将数据集展开为二维 DataFrame（行 = 时间×格点）。"""
        df: pd.DataFrame = self.ds.to_dataframe().reset_index()
        return df

    def to_json_records(self) -> List[Dict[str, Any]]:
        """将数据集转为 JSON 记录列表（NaN 统一替换为 None）。"""
        df = self.to_dataframe()
        df = df.where(pd.notnull(df), None)
        return df.to_dict(orient="records")

    def to_api_format(self) -> Dict[str, Any]:
        """生成达卯接口格式：{"data": [...], "units": {...}}。"""
        return {
            "data": self.to_json_records(),
            "units": self.extract_units(),
        }


# ===========================================================================
# Section 3: MongoDB 客户端
# ===========================================================================

class WeatherMongoClient:
    """MongoDB 天气数据库基础客户端。

    负责连接管理与通用查询；按格点/城镇/时间范围的专用查询见
    子类 ``WeatherMongoReader``。
    """

    def __init__(
        self,
        host: str = "123.60.41.219",
        port: int = 27017,
        database: str = "weather",
        username: str = "weather_app",
        password: str = "weather!@#123",
        timeout_ms: int = 5000,
    ):
        if not HAS_PYMONGO:
            raise ImportError("pymongo 未安装，请运行: pip install pymongo")

        # 用户名/密码含特殊字符（如 !@#），拼接 URI 前必须 URL 编码
        username_enc = quote_plus(username)
        password_enc = quote_plus(password)

        self.uri = (
            f"mongodb://{username_enc}:{password_enc}"
            f"@{host}:{port}/{database}"
        )
        self.client = MongoClient(
            self.uri,
            serverSelectionTimeoutMS=timeout_ms,
            connectTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
        )
        self.db = self.client[database]

    def list_collections(self) -> List[str]:
        """列出数据库中所有集合名。"""
        return self.db.list_collection_names()

    def find_to_df(
        self,
        collection: str,
        query: Optional[Dict] = None,
        projection: Optional[Dict] = None,
    ) -> pd.DataFrame:
        """查询集合并返回 DataFrame（空结果返回空帧；自动丢弃 _id 列）。"""
        if query is None:
            query = {}
        data = list(self.db[collection].find(query, projection))
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        if "_id" in df.columns:
            df.drop(columns=["_id"], inplace=True)
        return df

    def find_to_json(
        self,
        collection: str,
        query: Optional[Dict] = None,
        projection: Optional[Dict] = None,
    ) -> List[Dict]:
        """查询集合并返回 JSON 列表（默认排除 _id 字段）。"""
        if query is None:
            query = {}
        return list(self.db[collection].find(
            query, projection or {"_id": 0}
        ))


class WeatherMongoReader(WeatherMongoClient):
    """
    MongoDB 天气数据高级读取器。

    在 WeatherMongoClient 基础上提供按格点/城镇/时间范围的专用查询方法。
    """

    def __init__(
        self,
        host: str = "123.60.41.219",
        port: int = 27017,
        db_name: str = "weather",
        username: str = "weather_app",
        password: str = "weather!@#123",
        connect: bool = True,
    ):
        """
        Parameters
        ----------
        connect : bool
            是否立即连接。设为 False 可延迟连接（供模拟/测试用）。
        """
        if connect:
            super().__init__(host, port, db_name, username, password,
                             timeout_ms=30000)
            # 使用更长超时重连（socket 超时放宽到 300s，适应大查询）
            username_enc = quote_plus(username)
            password_enc = quote_plus(password)
            uri = (
                f"mongodb://{username_enc}:{password_enc}"
                f"@{host}:{port}/{db_name}"
                f"?authSource={db_name}"
            )
            self.client = MongoClient(
                uri,
                serverSelectionTimeoutMS=30000,
                socketTimeoutMS=300000,
                connectTimeoutMS=30000,
            )
            self.db = self.client[db_name]
            # 主动触发一次服务端往返，连接失败在此处即暴露
            self.db.list_collection_names()
            logger.info("MongoDB 连接成功")
        else:
            # 延迟连接模式：client/db 置空，由调用方后续注入（测试用）
            self.client = None  # type: ignore[assignment]
            self.db = None  # type: ignore[assignment]

    # =========================
    # 格点实况
    # =========================

    def get_real_by_point_and_time(
        self,
        lat: float,
        lon: float,
        start_time: int,
        end_time: int,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> pd.DataFrame:
        """单格点 + 时间范围查询 real_data 集合。"""
        # datatime 为整数时间编码（YYYYMMDDHH），区间闭式过滤
        query = {
            "lat": lat,
            "lon": lon,
            "datatime": {"$gte": start_time, "$lte": end_time},
        }
        cursor = (
            self.db["real_data"]
            .find(query, {"_id": 0})
            .sort("datatime", 1)
            .limit(limit)
        )
        return pd.DataFrame(list(cursor))

    def get_real_by_point_and_time_v2(
        self,
        lat: float,
        lon: float,
        start_time: int,
        end_time: int,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> pd.DataFrame:
        """单格点 + 时间范围查询 real_data_v2 集合。"""
        query = {
            "lat": lat,
            "lon": lon,
            "datatime": {"$gte": start_time, "$lte": end_time},
        }
        cursor = (
            self.db["real_data_v2"]
            .find(query, {"_id": 0})
            .sort("datatime", 1)
            .limit(limit)
        )
        return pd.DataFrame(list(cursor))

    # =========================
    # 城镇预报
    # =========================

    def get_forecast_by_areacode_and_time(
        self,
        areacode: str,
        start_time: str,
        end_time: str,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> pd.DataFrame:
        """单城镇 + 时间范围查询 forecast_data 集合。"""
        query = {
            "areacode": areacode,
            "datatime": {"$gte": start_time, "$lte": end_time},
        }
        cursor = (
            self.db["forecast_data"]
            .find(query, {"_id": 0})
            .sort("datatime", 1)
            .limit(limit)
        )
        return pd.DataFrame(list(cursor))

    def get_forecast_by_multi_areacode_and_time(
        self,
        areacode_list: List[str],
        start_time: str,
        end_time: str,
        limit: int = 20000,
    ) -> pd.DataFrame:
        """多城镇 + 时间范围批量查询 forecast_data 集合。"""
        # $in 批量匹配城镇清单，结果按城镇 + 时间双重排序
        query = {
            "areacode": {"$in": areacode_list},
            "datatime": {"$gte": start_time, "$lte": end_time},
        }
        cursor = (
            self.db["forecast_data"]
            .find(query, {"_id": 0})
            .sort([("areacode", 1), ("datatime", 1)])
            .limit(limit)
        )
        return pd.DataFrame(list(cursor))

    # =========================
    # 区域映射
    # =========================

    def get_areacodes_by_city(self, city_geocode: str) -> pd.DataFrame:
        """按城市地理编码查询下属城镇列表。"""
        cursor = self.db["area_data"].find(
            {"city_geocode": city_geocode},
            {"_id": 0, "areacode": 1, "district": 1},
        )
        return pd.DataFrame(list(cursor))


# ===========================================================================
# Section 4: 离线气象模拟器（替代 MongoDB 的数据提供者）
# ===========================================================================

class WeatherSimulator:
    """
    模拟气象数据提供者，替代 MongoDB。

    接口与 WeatherMongoReader 的查询方法一致，可直接替换使用。
    基于 make_sample_weather_dataset() 生成的合成数据。
    """

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)
        self._ds = make_sample_weather_dataset(seed=seed)
        self._df_cache: Optional[pd.DataFrame] = None

    @property
    def _flat_df(self) -> pd.DataFrame:
        """将 xr.Dataset 展平为类似 MongoDB 查询结果的 DataFrame（带缓存）。"""
        if self._df_cache is None:
            df = self._ds.to_dataframe().reset_index()
            # time → Mongo 风格的整数时间编码（YYYYMMDDHH）
            df["datatime"] = (
                df["time"]
                .dt.strftime("%Y%m%d%H")
                .astype(int)
            )
            self._df_cache = df
        return self._df_cache

    def get_real_by_point_and_time(
        self,
        lat: float,
        lon: float,
        start_time: int,
        end_time: int,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> pd.DataFrame:
        """模拟 real_data 单格点查询。"""
        df = self._flat_df
        # 请求坐标不一定落在格点上，吸附到最近格点
        nearest_lat = min(self._ds.lat.values, key=lambda x: abs(x - lat))
        nearest_lon = min(self._ds.lon.values, key=lambda x: abs(x - lon))

        mask = (
            (df["lat"] == nearest_lat)
            & (df["lon"] == nearest_lon)
            & (df["datatime"] >= start_time)
            & (df["datatime"] <= end_time)
        )
        result = df[mask].sort_values("datatime").head(limit).copy()
        # 与 Mongo 返回结构对齐：丢弃 xarray 原始 time 列
        if "time" in result.columns:
            result.drop(columns=["time"], inplace=True)
        return result

    def get_real_by_point_and_time_v2(
        self,
        lat: float,
        lon: float,
        start_time: int,
        end_time: int,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> pd.DataFrame:
        """模拟 real_data_v2 单格点查询（与 v1 相同实现）。"""
        return self.get_real_by_point_and_time(
            lat, lon, start_time, end_time, limit
        )

    def get_forecast_by_areacode_and_time(
        self,
        areacode: str,
        start_time: str,
        end_time: str,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> pd.DataFrame:
        """模拟 forecast_data 单城镇查询。"""
        df = self._flat_df
        # 字符串时间（如 "2024-01-01 08:00"）→ 整数编码 YYYYMMDDHH（取前 10 位）
        start_int = int(start_time.replace("-", "").replace(":", "").replace(" ", "")[:10])
        end_int = int(end_time.replace("-", "").replace(":", "").replace(" ", "")[:10])

        mask = (df["datatime"] >= start_int) & (df["datatime"] <= end_int)
        result = df[mask].head(limit).copy()
        if "time" in result.columns:
            result.drop(columns=["time"], inplace=True)
        # 模拟数据无真实城镇维度，直接把入参 areacode 打上
        result["areacode"] = areacode
        return result

    def get_forecast_by_multi_areacode_and_time(
        self,
        areacode_list: List[str],
        start_time: str,
        end_time: str,
        limit: int = 20000,
    ) -> pd.DataFrame:
        """模拟 forecast_data 多城镇批量查询。"""
        frames = []
        # limit 均摊到各城镇，保证单城镇查询不超过上限
        per_call = max(limit // len(areacode_list), 1)
        for ac in areacode_list:
            df = self.get_forecast_by_areacode_and_time(
                ac, start_time, end_time, per_call
            )
            if not df.empty:
                frames.append(df)
        if frames:
            return pd.concat(frames, ignore_index=True).sort_values(
                ["areacode", "datatime"]
            )
        return pd.DataFrame()


# ===========================================================================
# Section 5: 批量抓取与实测文件读取
# ===========================================================================

def get_real_for_points(
    reader,
    df_points: pd.DataFrame,
    start_time: int,
    end_time: int,
    limit_per_point: int = DEFAULT_QUERY_LIMIT,
) -> pd.DataFrame:
    """
    批量获取多格点的天气实况数据。

    reader 可为 WeatherMongoReader 或 WeatherSimulator，
    只要提供 get_real_by_point_and_time_v2() 方法即可（鸭子类型）。
    df_points 需含 lat/lon 列，可选 areacode 列（用于回填城镇标识）。
    """
    all_results = []

    for _, row in df_points.iterrows():
        lat = float(row["lat"])
        lon = float(row["lon"])
        areacode = row.get("areacode", None)

        df_single = reader.get_real_by_point_and_time_v2(
            lat=lat, lon=lon,
            start_time=start_time, end_time=end_time,
            limit=limit_per_point,
        )
        if df_single.empty:
            continue

        # 回填请求的精确坐标与城镇标识（reader 返回的可能是吸附后格点）
        df_single["lat"] = lat
        df_single["lon"] = lon
        if areacode is not None:
            df_single["areacode"] = areacode

        all_results.append(df_single)

    if all_results:
        return pd.concat(all_results, ignore_index=True)
    return pd.DataFrame()


def read_measured_folder(folder_path: str) -> pd.DataFrame:
    """
    从 Excel 文件夹读取统调负荷实测数据。

    自动识别各文件中包含"实测值"的列，并根据文件名提取日期，
    与时刻列组合生成完整 datetime。

    Returns
    -------
    pd.DataFrame
        列: ['datetime', 'value']
    """
    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"文件夹不存在: {folder_path}")

    all_data: List[pd.DataFrame] = []
    # 实测值列名模式：列名中包含"实测值"即命中
    pattern = re.compile(r".*实测值.*")

    for file in sorted(os.listdir(folder_path)):
        if not file.endswith(".xlsx"):
            continue

        # 文件名（去扩展名）即日期，如 "2024-01-01.xlsx"
        date_str = os.path.splitext(file)[0]
        try:
            base_date = pd.to_datetime(date_str)
        except (ValueError, TypeError):
            logger.warning("文件名无法解析日期：%s", file)
            continue

        file_path = os.path.join(folder_path, file)
        df = pd.read_excel(file_path)

        # 查找时刻列（列名含"时"/"间"/"time"，找不到则回退首列）
        time_col_candidates = [
            c for c in df.columns
            if any(kw in str(c) for kw in ("时", "间", "time"))
        ]
        time_col = (
            time_col_candidates[0] if time_col_candidates else df.columns[0]
        )

        # 查找实测值列
        measured_cols = [c for c in df.columns if pattern.match(str(c))]
        if not measured_cols:
            logger.warning("未找到实测值列：%s", file)
            continue
        measured_col = measured_cols[0]

        # 日期 + 时刻字符串拼成完整 datetime
        df["datetime"] = df[time_col].apply(
            lambda t: pd.to_datetime(f"{date_str} {t}")
        )
        df_result = df[["datetime", measured_col]].rename(
            columns={measured_col: "value"}
        )
        all_data.append(df_result)

    if not all_data:
        return pd.DataFrame(columns=["datetime", "value"])

    return (
        pd.concat(all_data)
        .sort_values("datetime")
        .reset_index(drop=True)
    )
