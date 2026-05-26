from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pvlib

from .solar_simulation import SolarSimulator


@dataclass(slots=True)
class PVProfileConfig:
    latitude: float
    longitude: float
    timezone: str
    capacity_kwp: float
    tilt: float | None
    azimuth: float
    system_loss: float
    temp_coeff: float
    cloud_factor: float | None
    mode: str


@dataclass(slots=True)
class RenewableProfileResult:
    power_series: pd.Series
    metadata: dict[str, float | str]
    quality_flags: pd.DataFrame | None = None


def simulate_pv_clear_sky(time_index: pd.DatetimeIndex, config: PVProfileConfig) -> pd.Series:
    index = pd.to_datetime(time_index)
    if index.tz is None:
        index = index.tz_localize(config.timezone)
    location = pvlib.location.Location(config.latitude, config.longitude, tz=config.timezone)
    solar_position = location.get_solarposition(index)
    clear_sky = location.get_clearsky(index, model="ineichen")
    tilt = config.tilt if config.tilt is not None else abs(config.latitude)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=config.azimuth,
        solar_zenith=solar_position["zenith"],
        solar_azimuth=solar_position["azimuth"],
        dni=clear_sky["dni"],
        ghi=clear_sky["ghi"],
        dhi=clear_sky["dhi"],
    )
    cloud_factor = 1.0 if config.cloud_factor is None else config.cloud_factor
    poa_global = poa["poa_global"].clip(lower=0) * cloud_factor
    temp_cell = pvlib.temperature.pvsyst_cell(poa_global, temp_air=30, wind_speed=1)
    dc = pvlib.pvsystem.pvwatts_dc(
        poa_global,
        temp_cell,
        pdc0=1000,
        gamma_pdc=config.temp_coeff,
    )
    ac = pvlib.inverter.pvwatts(dc, pdc0=1000) * (1 - config.system_loss)
    pv_kw = (ac / 1000.0) * config.capacity_kwp
    return pv_kw.clip(lower=0, upper=config.capacity_kwp).rename("pv_kw").tz_localize(None)


def simulate_pv_from_weather(weather_df: pd.DataFrame, config: PVProfileConfig) -> pd.Series:
    simulator = SolarSimulator(
        latitude=config.latitude,
        longitude=config.longitude,
        timezone=config.timezone,
        tilt=config.tilt,
        azimuth=config.azimuth,
    )
    capacity_mw = config.capacity_kwp / 1000.0
    result = simulator.simulate(
        weather_df=weather_df,
        equiv_hours=float(weather_df.attrs.get("equiv_hours", 1200.0)),
        target_capacity_mw=capacity_mw,
    )
    return (result.output_mw * 1000.0).rename("pv_kw")


def validate_equivalent_hours(power_series: pd.Series, capacity_kwp: float) -> float:
    if len(power_series) < 2:
        return 0.0
    dt_hours = (power_series.index[1] - power_series.index[0]).total_seconds() / 3600.0
    annual_kwh = float((power_series * dt_hours).sum())
    return annual_kwh / capacity_kwp if capacity_kwp > 0 else 0.0


def load_or_build_pv_profile(
    config: PVProfileConfig,
    time_index: pd.DatetimeIndex | None = None,
    weather_df: pd.DataFrame | None = None,
    cache_path: str | Path | None = None,
) -> RenewableProfileResult:
    if cache_path is not None and Path(cache_path).exists():
        cached = pd.read_csv(cache_path, index_col="timestamp", parse_dates=True)
        series = cached.iloc[:, 0].rename("pv_kw")
    elif config.mode == "clear_sky":
        if time_index is None:
            raise ValueError("time_index is required for clear_sky mode")
        series = simulate_pv_clear_sky(pd.DatetimeIndex(time_index), config)
    elif config.mode == "weather_driven":
        if weather_df is None:
            raise ValueError("weather_df is required for weather_driven mode")
        series = simulate_pv_from_weather(weather_df, config)
    elif config.mode == "replay":
        if weather_df is None or "pv_kw" not in weather_df.columns:
            raise ValueError("replay mode requires weather_df with pv_kw column")
        series = weather_df["pv_kw"].copy()
    else:
        raise ValueError(f"unsupported PV profile mode: {config.mode}")

    if cache_path is not None and not Path(cache_path).exists():
        output = Path(cache_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        series.rename("pv_kw").to_frame().to_csv(output, index_label="timestamp")

    metadata = {
        "mode": config.mode,
        "capacity_kwp": config.capacity_kwp,
        "equivalent_hours": validate_equivalent_hours(series, config.capacity_kwp),
    }
    quality_flags = pd.DataFrame(
        {"timestamp": pd.to_datetime(series.index), "quality_score": [1.0] * len(series)}
    )
    return RenewableProfileResult(power_series=series, metadata=metadata, quality_flags=quality_flags)
