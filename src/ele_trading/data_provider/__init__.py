"""data_provider 包公开入口：交易数据接入与质量边界。

职责：把市场、气象和资产输入转换为带 ``as_of`` 与版本信息的活动交易数据
（``MarketDataSnapshot``）。投资测算 case 与用户侧样例不属于活动 API，
归档于 ``todo/`` 子包。

分层说明：
- 契约与类型：``contracts``（MarketDataSnapshot）、``schemas``、``asset_data``；
- 实现：``market_data``（市场快照/CSV 读取）、``quality``（时序质量工具）；
- 气象：``weather_data`` 为聚合 facade，实现位于 ``resource_weather`` / ``weather_io``；
- 样例：``sample_data`` 提供 data/trading/ 最小样例的加载入口。
"""

# 子模块句柄（便于 data_provider.market_data 等形式访问）
from . import asset_data, market_data, quality, weather_data

# --- 资产配置 ---
from .asset_data import BESSConfig, load_bess_config

# --- 核心契约 ---
from .contracts import (
    DataAvailabilityRecord,
    MarketDataSnapshot,
    RuleSnapshot,
)

# --- 市场数据实现 ---
from .market_data import (
    build_trading_case_dataset,
    load_market_data_csv,
    load_observed_power_series,
    load_price_series,
)

# --- 气象数据（经 weather_data 聚合 re-export） ---
from .weather_data import (
    DEFAULT_LAG,
    DEFAULT_LATS,
    DEFAULT_LONS,
    DEFAULT_QUERY_LIMIT,
    WEATHER_VARS,
    NetCDFToJSON,
    WeatherMongoClient,
    WeatherMongoReader,
    WeatherSimulator,
    fetch_weather_open_meteo,
    get_real_for_points,
    load_weather_csv,
    make_sample_load_data,
    make_sample_weather_dataset,
    read_measured_folder,
    save_weather_csv,
)

# --- 内置最小样例 ---
from .sample_data import get_sample_paths

# --- 活动数据类型 ---
from .schemas import (
    ObservedPowerSeries,
    PriceSeries,
)

# --- 时序质量工具 ---
from .quality import (
    align_series_on_timestamp,
    compute_quality_score,
    detect_step_jumps,
    detect_zero_values,
    repair_anomalies,
    resample_series_frame,
)
