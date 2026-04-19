from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from windpowerlib import ModelChain, WindFarm, WindTurbine, get_turbine_types


# 综合折减系数（尾流损耗 + 可用率 + 集电线损等）
_SYSTEM_EFF = 0.92

# 风切变指数（1/7 幂律，适用于平原/草原地区）
_SHEAR_EXPONENT = 1 / 7


@dataclass(slots=True)
class WindSimResult:
    output_mw: pd.Series          # 出力时序（MW），与输入气象数据同频
    total_generation_mwh: float   # 模拟年发电量（MWh）
    scale_factor: float           # 等效小时数校准系数 K
    selected_turbine: str         # 选用的机型名称
    turbine_count: int            # 所需台数（= ceil(target_capacity / single_turbine_mw)）


class WindSimulator:
    """基于 windpowerlib 的风电出力模拟器。

    使用物理仿真（功率曲线 + 轮毂高度外推）+ 等效小时数校准，
    生成指定容量的出力时间序列。
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        hub_height: float = 100.0,
        ref_height: float = 10.0,
    ) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.hub_height = hub_height
        self.ref_height = ref_height

    def simulate(
        self,
        weather_df: pd.DataFrame,
        equiv_hours: float,
        target_capacity_mw: float = 1.0,
    ) -> WindSimResult:
        """模拟风电出力时序。

        Args:
            weather_df: 气象数据，索引为 DatetimeIndex（含时区），至少包含列：
                        wind_speed（m/s，参考高度 ref_height）、
                        temperature（°C）、pressure（Pa）。
            equiv_hours: 目标年等效利用小时数，用于校准出力曲线。
            target_capacity_mw: 目标装机容量（MW），默认 1 MW。

        Returns:
            WindSimResult with output_mw, total_generation_mwh, scale_factor,
            selected_turbine, turbine_count.
        """
        turbine_type, turbine_mw = _select_turbine(self.hub_height)
        n_turbines = max(1, int(np.ceil(target_capacity_mw / turbine_mw)))

        turbine = WindTurbine(turbine_type=turbine_type, hub_height=self.hub_height)

        # windpowerlib 要求 MultiIndex 气象数据：(variable, height)
        # 风速已外推至 hub_height，ModelChain 直接使用，无需 roughness_length
        weather_wpl = _build_weather_multiindex(weather_df, self.ref_height, self.hub_height)

        mc = ModelChain(turbine).run_model(weather_wpl)
        # power_output 单位：W（单台机组）
        power_w = mc.power_output.clip(lower=0)
        # 归一化为每 MW 装机的出力（capacity factor 形式）
        capacity_factor = power_w / (turbine_mw * 1e6)  # dimensionless [0, 1]

        # 校准：根据等效小时数计算系数 K（基于 1 MW 基准）
        dt_hours = _infer_dt_hours(weather_df.index)
        e_raw_per_mw = (capacity_factor * _SYSTEM_EFF).sum() * dt_hours  # MWh/MW
        K = equiv_hours / e_raw_per_mw if e_raw_per_mw > 0 else 1.0

        # 应用校准系数并缩放到目标容量（线性）
        output_mw = capacity_factor * _SYSTEM_EFF * K * target_capacity_mw
        output_mw.name = "wind_output_mw"

        return WindSimResult(
            output_mw=output_mw,
            total_generation_mwh=float(output_mw.sum() * dt_hours),
            scale_factor=K,
            selected_turbine=turbine_type,
            turbine_count=n_turbines,
        )


@lru_cache(maxsize=8)
def _select_turbine(hub_height: float) -> tuple[str, float]:
    """从 windpowerlib 内置库中选择最接近中位功率的机型。

    windpowerlib 0.2.2 的 get_turbine_types() 不含 rated_power 列，
    需逐一实例化 WindTurbine 以读取 nominal_power（W）。

    Returns:
        (turbine_type, rated_power_mw)
    """
    df = get_turbine_types(print_out=False)
    powers: dict[str, float] = {}
    for ttype in df['turbine_type']:
        try:
            t = WindTurbine(turbine_type=ttype, hub_height=hub_height)
            powers[ttype] = t.nominal_power / 1e6  # MW
        except Exception:
            pass

    power_series = pd.Series(powers)
    median_mw = power_series.median()
    selected = (power_series - median_mw).abs().idxmin()
    return selected, float(power_series[selected])


def _build_weather_multiindex(
    weather_df: pd.DataFrame,
    ref_height: float,
    hub_height: float,
) -> pd.DataFrame:
    """将气象 DataFrame 转换为 windpowerlib 要求的 MultiIndex 格式。

    windpowerlib ModelChain 期望列索引为 (variable, height) 二级 MultiIndex。
    当 weather_df 中 wind_speed 的高度等于 hub_height 时，ModelChain 直接使用，
    无需 roughness_length 列（避免 windpowerlib 0.2.2 的对数/幂律外推依赖）。

    风切变已在此函数中用幂律（1/7 指数）从 ref_height 外推到 hub_height。
    """
    ws_ref = weather_df['wind_speed']
    ws_hub = ws_ref * (hub_height / ref_height) ** _SHEAR_EXPONENT

    temp_k = weather_df['temperature'] + 273.15  # °C → K

    data = {
        ('wind_speed', hub_height): ws_hub,
        ('temperature', hub_height): temp_k,
        ('pressure', 0.0): weather_df['pressure'],
    }
    multi_df = pd.DataFrame(data)
    multi_df.columns = pd.MultiIndex.from_tuples(multi_df.columns)
    return multi_df


def _infer_dt_hours(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    diffs = pd.Series(index[1:]) - pd.Series(index[:-1])
    return float(diffs.median().total_seconds() / 3600.0)
