from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from windpowerlib import ModelChain, WindTurbine

from .models import SimulationResult
from .weather import fetch_weather_open_meteo


@dataclass(slots=True)
class WindProfileConfig:
    """
    定义风场级别的关键参数，如装机规模、轮毂高度、功率曲线参数、等效利用小时数目标、峰值比例限制等
    """
    year: int  # 仿真目标年份，用于确定气象数据取值范围
    freq: str  # 时间分辨率，如 "1h"、"5min"，决定输出时序粒度
    farm_capacity_mw: float  # 风场总装机容量 (MW)

    # 校正目标
    # old_name: mean_wind_speed_140m
    mean_wind_speed_target: float | None  # 测风塔高度处的年均风速校准目标 (m/s)，None 表示不校准
    # old_name: eq_full_load_hours
    target_full_load_hours: float | None  # 年等效满发小时数目标 (h)，用于二次标定发电量
    # 高度与地形
    meteo_height_m: float  # 气象数据的风速测量高度 (m)，如 Open-Meteo 的 100m
    met_mast_height_m: float  # 测风塔高度 (m)，用于风速外推的中间参考高度
    hub_height_m: float  # 风机轮毂高度 (m)，最终风速外推到此高度
    shear_alpha: float  # 风切变指数，幂律风速廓线的高度换算参数

    # 风机（≤8MW，低风速）
    rated_power_kw: float  # 单机额定功率 (kW)
    cut_in: float  # 切入风速 (m/s)，低于此风速风机不发电
    rated_speed: float  # 额定风速 (m/s)，达到此风速后出力恒定在额定值
    cut_out: float  # 切出风速 (m/s)，超过此风速风机停机保护

    # 峰值 ≤ 1.2 × 装机
    max_power_ratio: float  # 最大出力与装机容量的比值上限，用于削峰约束

    mode: str  # 运行模式: "resource_simulation" 自动获取气象数据仿真


def power_law(speed: pd.Series, h_from: float, h_to: float, alpha: float) -> pd.Series:
    """
    幂律
    """
    return speed * (h_to / h_from) ** alpha


def build_turbine(config: WindProfileConfig) -> WindTurbine:
    """
    风机
    """
    wind_speed = np.arange(0, 31, 0.5)
    power_kw = np.zeros_like(wind_speed)
    
    ramp_mask = (wind_speed >= config.cut_in) & (wind_speed < config.rated_speed)
    power_kw[ramp_mask] = (
        ((wind_speed[ramp_mask] - config.cut_in) / (config.rated_speed - config.cut_in)) ** 3
        * config.rated_power_kw
    )
    
    rated_mask = (wind_speed >= config.rated_speed) & (wind_speed < config.cut_out)
    power_kw[rated_mask] = config.rated_power_kw
    
    return WindTurbine(
        hub_height=config.hub_height_m,
        nominal_power=config.rated_power_kw,
        power_curve=pd.DataFrame({"wind_speed": wind_speed, "value": power_kw}),
    )


def prepare_wind_weather_frame(weather_df: pd.DataFrame, config: WindProfileConfig) -> pd.DataFrame:
    if "wind_speed_100m" not in weather_df.columns:
        raise ValueError("weather_df must contain wind_speed_100m")
    if "temperature_2m" not in weather_df.columns:
        raise ValueError("weather_df must contain temperature_2m")

    df = weather_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "timestamp" in df.columns:
            df = df.set_index(pd.to_datetime(df["timestamp"]))
        else:
            raise TypeError("weather_df must have DatetimeIndex or timestamp column")
    df = df.sort_index()
    
    # 风速处理
    ws_100 = df["wind_speed_100m"].clip(lower=0)
    ws_140 = power_law(ws_100, config.meteo_height_m, config.met_mast_height_m, config.shear_alpha)
    if config.mean_wind_speed_target is not None and ws_140.mean() > 0:
        ws_140 = ws_140 * (config.mean_wind_speed_target / ws_140.mean())
    ws_hub = power_law(ws_140, config.met_mast_height_m, config.hub_height_m, config.shear_alpha)
    
    # 单机功率
    prepared = pd.DataFrame(
        {
            ("wind_speed", config.hub_height_m): ws_hub.values,
            ("temperature", 2.0): (df["temperature_2m"] + 273.15).values,
            ("pressure", 0.0): np.full(len(df), 101325.0),
        },
        index=df.index,
    )
    prepared.columns = pd.MultiIndex.from_tuples(prepared.columns)
    
    return prepared


def rescale_wind_output_to_target_flh(
    power_mw: np.ndarray,
    dt_hours: float,
    config: WindProfileConfig,
) -> np.ndarray:
    """
    目标：
        1) 峰值 ≤ max_ratio × cap_mw
        2) 年 FLH ≈ target_flh
        3) 能量守恒（削峰后通过未饱和时段回补）

    Args:
        power_mw (np.ndarray): 原始风场出力时序 (MW)
        dt_hours (float): 时间步长 (小时)
        config (WindProfileConfig): 风场配置，含装机容量、满发小时数目标、峰值比例上限

    Returns:
        np.ndarray: 标定后的风场出力时序 (MW)，满足峰值约束且年发电量接近目标
    """
    if config.target_full_load_hours is None:
        return np.clip(power_mw, 0.0, config.farm_capacity_mw * config.max_power_ratio)

    target_energy = config.farm_capacity_mw * config.target_full_load_hours
    max_power = config.farm_capacity_mw * config.max_power_ratio
    series = power_mw.copy()
    for _ in range(8):
        # 全局缩放到目标能量
        current_energy = series.sum() * dt_hours
        if current_energy <= 0:
            break
        series *= target_energy / current_energy
        # 峰值约束
        clipped = np.minimum(series, max_power)
        # 计算当前 FLH, 回补被削掉的能量
        deficit = target_energy - clipped.sum() * dt_hours
        if abs(deficit) <= config.farm_capacity_mw * 0.5:
            return clipped
        margin = np.maximum(max_power - clipped, 0.0)
        if margin.sum() <= 0:
            return clipped
        series = clipped + margin / margin.sum() * (deficit / dt_hours)
    
    return np.minimum(series, max_power)


def simulate_wind_farm_output(weather_df: pd.DataFrame, config: WindProfileConfig) -> pd.Series:
    # 单机功率
    prepared = prepare_wind_weather_frame(weather_df, config)
    # 风机
    turbine = build_turbine(config)
    chain = ModelChain(turbine).run_model(prepared)
    single_turbine_kw = chain.power_output.values
    # 风场聚合
    turbine_count = max(1, round(config.farm_capacity_mw * 1000.0 / config.rated_power_kw))
    farm_output_mw = single_turbine_kw * turbine_count / 1000.0
    # 二次标定（核心）
    dt_hours = (prepared.index[1] - prepared.index[0]).total_seconds() / 3600.0
    scaled = rescale_wind_output_to_target_flh(farm_output_mw, dt_hours, config)
    # 输出
    return pd.Series(np.clip(scaled, 0.0, None), index=prepared.index, name="wind_kw") * 1000.0


def load_or_build_wind_profile(
    config: WindProfileConfig,
    weather_df: pd.DataFrame | None = None,
    cache_path: str | Path | None = None,
) -> SimulationResult:
    if cache_path is not None and Path(cache_path).exists():
        cached = pd.read_csv(cache_path, index_col="timestamp", parse_dates=True)
        series = cached.iloc[:, 0].rename("wind_kw")
    else:
        if weather_df is None:
            if config.mode != "resource_simulation":
                raise ValueError("weather_df is required unless resource_simulation fetch is used")
            weather_df = fetch_weather_open_meteo(
                latitude=float(getattr(config, "latitude", 0.0)),
                longitude=float(getattr(config, "longitude", 0.0)),
                start_date=f"{config.year}-01-01",
                end_date=f"{config.year}-12-31",
                hourly_fields=["wind_speed_100m", "temperature_2m"],
            )
            weather_df = weather_df.set_index("timestamp")
        series = simulate_wind_farm_output(weather_df, config)
    # 数据保存
    if cache_path is not None and not Path(cache_path).exists():
        output = Path(cache_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        series.rename("wind_kw").to_frame().to_csv(output, index_label="timestamp")
    # 推断时间步长
    dt_hours = 1.0
    if len(series) > 1:
        dt_hours = (series.index[1] - series.index[0]).total_seconds() / 3600.0
    # 等效小时数
    equivalent_hours = (series.sum() * dt_hours / 1000.0) / config.farm_capacity_mw if config.farm_capacity_mw > 0 else 0.0
    # 总发电量
    total_generation_mwh = float(series.sum() * dt_hours / 1000.0)
    # 元数据
    metadata = {
        "mode": config.mode,
        "farm_capacity_mw": config.farm_capacity_mw,
        "equivalent_hours": float(equivalent_hours),
    }
    return SimulationResult(
        power_series=series,
        total_generation_mwh=total_generation_mwh,
        scale_factor=1.0,
        metadata=metadata,
    )
