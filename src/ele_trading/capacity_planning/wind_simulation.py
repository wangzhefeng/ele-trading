from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from windpowerlib import ModelChain, WindTurbine, get_turbine_types


# 综合折减系数（尾流损耗 + 可用率 + 集电线损等）
_SYSTEM_EFF = 0.92


@dataclass(slots=True)
class WindSimResult:
    output_mw: pd.Series          # 出力时序（MW），与输入气象数据同频
    total_generation_mwh: float   # 模拟年发电量（MWh）
    scale_factor: float           # 等效小时数校准系数 K
    selected_turbine: str         # 选用的机型名称
    turbine_count: int            # 风机台数


class WindSimulator:
    """基于 windpowerlib 的风电出力模拟器。

    使用物理仿真（功率曲线 + 轮毂高度外推）+ 等效小时数校准，
    生成指定容量的出力时间序列。
    """

    def __init__(
        self,
        hub_height: float = 100.0,
        wind_shear_exp: float = 0.143,
        wind_speed_ref_height: float = 10.0,
    ) -> None:
        if wind_speed_ref_height <= 0:
            raise ValueError(f'wind_speed_ref_height must be > 0, got {wind_speed_ref_height}')
        self.hub_height = hub_height
        self.wind_shear_exp = wind_shear_exp
        self.wind_speed_ref_height = wind_speed_ref_height

    def simulate(
        self,
        weather_df: pd.DataFrame,
        equiv_hours: float,
        target_capacity_mw: float = 1.0,
        single_turbine_capacity_mw: float | None = None,
    ) -> WindSimResult:
        """模拟风电场出力时间序列。

        Args:
            weather_df: 时间序列 DataFrame，含 'wind_speed'（m/s）、
                        'temperature'（°C）、'pressure'（Pa）列。
            equiv_hours: 年等效利用小时数，用于校准出力曲线。
            target_capacity_mw: 风电场总装机容量（MW）。
            single_turbine_capacity_mw: 指定单机容量（MW），None 则自动匹配中位数机型。

        Returns:
            WindSimResult with output_mw, total_generation_mwh, scale_factor,
            selected_turbine, turbine_count.
        """
        turbine_type, rated_mw = _select_turbine(self.hub_height, single_turbine_capacity_mw)
        turbine_count = max(1, round(target_capacity_mw / rated_mw))

        turbine = WindTurbine(turbine_type=turbine_type, hub_height=self.hub_height)
        weather_wpl = _build_weather_multiindex(
            weather_df, self.wind_speed_ref_height, self.hub_height, self.wind_shear_exp
        )

        mc = ModelChain(turbine).run_model(weather_wpl)
        # power_output 单位：W（单台机组）
        power_w = mc.power_output.clip(lower=0)
        capacity_factor = (power_w / (rated_mw * 1e6)).clip(upper=1.0)  # 归一化为 [0, 1]

        dt_hours = _infer_dt_hours(weather_df.index)
        e_raw_per_mw = (capacity_factor * _SYSTEM_EFF).sum() * dt_hours  # MWh/MW
        K = equiv_hours / e_raw_per_mw if e_raw_per_mw > 0 else 1.0

        output_mw = capacity_factor * _SYSTEM_EFF * K * target_capacity_mw
        output_mw.name = "wind_output_mw"

        return WindSimResult(
            output_mw=output_mw,
            total_generation_mwh=float(output_mw.sum() * dt_hours),
            scale_factor=K,
            selected_turbine=turbine_type,
            turbine_count=turbine_count,
        )


@lru_cache(maxsize=32)
def _select_turbine(
    hub_height: float,
    single_turbine_capacity_mw: float | None,
) -> tuple[str, float]:
    """从 windpowerlib 内置库中选择风机型号，返回 (型号名, 额定功率 MW)。

    windpowerlib 0.2.2 的 get_turbine_types() 不含 rated_power 列，
    需逐一实例化 WindTurbine 读取 nominal_power（W）。结果由 lru_cache 缓存。
    """
    df = get_turbine_types(print_out=False)
    powers: dict[str, float] = {}
    for ttype in df['turbine_type']:
        try:
            t = WindTurbine(turbine_type=ttype, hub_height=hub_height)
            powers[ttype] = t.nominal_power / 1e6  # MW
        except Exception:
            pass

    if not powers:
        raise RuntimeError(
            f'No turbines found for hub_height={hub_height}m. '
            'Check that windpowerlib turbine data is installed.'
        )
    power_series = pd.Series(powers)
    if single_turbine_capacity_mw is not None:
        selected = (power_series - single_turbine_capacity_mw).abs().idxmin()
    else:
        selected = (power_series - power_series.median()).abs().idxmin()
    return selected, float(power_series[selected])


def _build_weather_multiindex(
    weather_df: pd.DataFrame,
    ref_height: float,
    hub_height: float,
    shear_exp: float,
) -> pd.DataFrame:
    """将气象 DataFrame 转换为 windpowerlib ModelChain 所需的 MultiIndex 列格式。

    风速用幂律从 ref_height 外推至 hub_height，使 ModelChain 直接使用已外推数据，
    无需 roughness_length 列（避免 windpowerlib 0.2.2 内置对数外推的依赖）。
    """
    ws_hub = weather_df['wind_speed'] * (hub_height / ref_height) ** shear_exp
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
    return float((index[1:] - index[:-1]).median().total_seconds() / 3600.0)
