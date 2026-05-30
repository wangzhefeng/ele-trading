from dataclasses import dataclass
from typing import List

import pandas as pd


@dataclass(slots=True)
class PriceSeries:
    """价格序列数据结构。"""

    timestamps: List[int]
    prices: List[float]
    label: str = "sample"


@dataclass(slots=True)
class BESSConfig:
    """储能物理约束与效率参数。"""

    asset_name: str
    soc0: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float
    eta_dis: float
    deg_cost: float
    dt: float


@dataclass(slots=True)
class ScenarioRecord:
    """单条场景记录。"""

    scenario: str
    hour: int
    price: float
    weight: float


@dataclass(slots=True)
class LoadProfileBuildConfig:
    target_year: int
    freq: str
    date_col: str
    time_col: str
    power_col: str
    monthly_energy_targets: dict[int, float] | None
    history_source_year: int | None
    history_source_month_start: int | None
    smoothing_window: int
    fill_missing_points: bool
    fill_missing_days: bool


@dataclass(slots=True)
class LoadProfileResult:
    data: pd.DataFrame
    summary: dict[str, float | int | str]


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


@dataclass(slots=True)
class CaseDatasetConfig:
    mode: str
    freq: str
    include_load: bool
    include_pv: bool
    include_wind: bool
    include_prices: bool


@dataclass(slots=True)
class CaseDataset:
    frame: pd.DataFrame
    metadata: dict[str, str | float | int]
