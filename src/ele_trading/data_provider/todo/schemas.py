"""Archived investment profile and resource-simulation contracts."""

from dataclasses import dataclass

import pandas as pd


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
class WindProfileConfig:
    year: int
    freq: str
    farm_capacity_mw: float
    target_full_load_hours: float | None
    mean_wind_speed_target: float | None
    meteo_height_m: float
    met_mast_height_m: float
    hub_height_m: float
    shear_alpha: float
    rated_power_kw: float
    cut_in: float
    rated_speed: float
    cut_out: float
    max_power_ratio: float
    mode: str


@dataclass(slots=True)
class RenewableProfileResult:
    power_series: pd.Series
    metadata: dict[str, float | str]
    quality_flags: pd.DataFrame | None
