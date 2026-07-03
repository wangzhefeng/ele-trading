from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pvlib

from .models import SimulationResult


# SAPM 开放架构组件（open-rack glass-glass）典型参数
_SAPM_PARAMS = {"a": -3.56, "b": -0.075, "deltaT": 3}

# PVWatts DC 模型参数
_GAMMA_PDC = -0.004  # 功率温度系数 [1/°C]
# 系统综合效率（逆变器 × 线损 × 其他损耗）
_SYSTEM_EFF = 0.96


class PVSimulator:
    """基于 pvlib 的光伏出力模拟器。

    使用物理仿真 + 等效小时数校准，生成指定容量的出力时间序列。
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        timezone: str = 'Asia/Shanghai',
        tilt: float | None = None,
        azimuth: float = 180.0,
        altitude: float = 0.0,
    ) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = timezone
        self.tilt = tilt if tilt is not None else latitude * 0.9
        self.azimuth = azimuth
        self._location = pvlib.location.Location(
            latitude=latitude,
            longitude=longitude,
            tz=timezone,
            altitude=altitude,
        )

    def simulate(
        self,
        weather_df: pd.DataFrame,
        equiv_hours: float,
        target_capacity_mw: float = 1.0,
    ) -> SimulationResult:
        """模拟光伏出力时序。

        Args:
            weather_df: 气象数据，索引为 DatetimeIndex（含时区），至少包含列：
                        ghi（W/m²）、temp_air（°C）、wind_speed（m/s）。
            equiv_hours: 目标年等效发电小时数，用于校准出力曲线。
            target_capacity_mw: 目标装机容量（MW），默认 1 MW。

        Returns:
            SimulationResult with power_series (kW), total_generation_mwh, scale_factor.
        """
        # 1. 计算太阳位置
        solar_pos = self._location.get_solarposition(weather_df.index)

        # 2. GHI → DNI + DHI（DISC 模型）
        disc_out = pvlib.irradiance.disc(
            ghi=weather_df["ghi"],
            solar_zenith=solar_pos["zenith"],
            datetime_or_doy=weather_df.index,
        )
        dni = disc_out["dni"].clip(lower=0)
        # GHI = DNI·cos(θ) + DHI; clip to 0 to avoid negative DHI at zenith ≥ 90°
        dhi = (weather_df["ghi"] - dni * _cos_zenith(solar_pos["zenith"])).clip(lower=0)

        # 3. 斜面辐照度（Hay-Davies 模型，需要 dni_extra）
        dni_extra = pvlib.irradiance.get_extra_radiation(weather_df.index)
        poa = pvlib.irradiance.get_total_irradiance(
            surface_tilt=self.tilt,
            surface_azimuth=self.azimuth,
            solar_zenith=solar_pos["zenith"],
            solar_azimuth=solar_pos["azimuth"],
            dni=dni,
            ghi=weather_df["ghi"],
            dhi=dhi,
            dni_extra=dni_extra,
            model="haydavies",
        )

        # 4. 组件温度（SAPM 模型）
        temp_cell = pvlib.temperature.sapm_cell(
            poa_global=poa["poa_global"],
            temp_air=weather_df["temp_air"],
            wind_speed=weather_df["wind_speed"],
            **_SAPM_PARAMS,
        )

        # 5. DC 功率（PVWatts，以 1 MW = 1e6 W 为基准）
        pdc0 = 1e6  # W，对应 1 MW 装机
        pdc = pvlib.pvsystem.pvwatts_dc(
            effective_irradiance=poa["poa_global"],
            temp_cell=temp_cell,
            pdc0=pdc0,
            gamma_pdc=_GAMMA_PDC,
        )

        # 6. AC 出力（应用系统综合效率），转换为 MW
        pac_mw = pdc * _SYSTEM_EFF / 1e6  # MW
        pac_mw = pac_mw.clip(lower=0)

        # 7. 校准：根据等效小时数计算系数 K
        # 采样间隔（小时）
        dt_hours = _infer_dt_hours(weather_df.index)
        e_raw = pac_mw.sum() * dt_hours  # MWh（原始年发电量）
        e_target = 1.0 * equiv_hours     # MWh（目标，基于 1 MW 基准容量）
        K = e_target / e_raw if e_raw > 0 else 1.0

        # 8. 应用校准系数并缩放到目标容量，转为 kW
        output_kw = pac_mw * K * target_capacity_mw * 1000.0
        output_kw.name = "pv_kw"

        return SimulationResult(
            power_series=output_kw,
            total_generation_mwh=float(output_kw.sum() * dt_hours / 1000.0),
            scale_factor=K,
        )


def _cos_zenith(zenith_deg: pd.Series) -> pd.Series:
    return np.cos(np.radians(zenith_deg))


def _infer_dt_hours(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    # Use median interval to tolerate DST gaps at position 0
    diffs = pd.Series(index[1:]) - pd.Series(index[:-1])
    return float(diffs.median().total_seconds() / 3600.0)
