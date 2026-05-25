from dataclasses import dataclass
from typing import Any, List


@dataclass(slots=True)
class StorageArbitrageResult:
    objective: float
    p_ch: List[float]
    p_dis: List[float]
    soc: List[float]


@dataclass(slots=True)
class MPCStepResult:
    step: int
    price: float
    p_ch: float
    p_dis: float
    soc_next: float
    step_objective: float


@dataclass(slots=True)
class UserSideStorageParams:
    capacity: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float = 0.95
    eta_dis: float = 0.95


@dataclass(slots=True)
class UserSideStorageDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    storage: UserSideStorageParams
    initial_soc: float
    demand_charge_rate: float
    step_hours: float
    terminal_soc_target: float | None = None
    cycle_cost_rate: float = 0.0


@dataclass(slots=True)
class UserSideStorageDispatchResult:
    charge_power: list[float]
    discharge_power: list[float]
    net_storage_power: list[float]
    soc: list[float]
    grid_import: list[float]
    max_grid_import: float
    energy_cost: float
    demand_cost: float
    total_cost: float
    constraint_violations: dict[str, float]
