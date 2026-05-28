from dataclasses import dataclass, field
from typing import Any, List


@dataclass(slots=True)
class StorageArbitrageResult:
    objective: float
    p_ch: List[float]
    p_dis: List[float]
    soc: List[float]


@dataclass(slots=True)
class CapacitySizingResult:
    """储能容量+调度联合优化结果。"""
    feasible: bool
    solver_status: str = ""
    optimal_power_kw: float = 0.0
    optimal_capacity_kwh: float = 0.0
    net_objective_yuan: float = 0.0
    annualized_capex_yuan: float = 0.0
    charge_schedule: List[float] = field(default_factory=list)
    discharge_schedule: List[float] = field(default_factory=list)
    soc_schedule: List[float] = field(default_factory=list)
    charge_kwh: float = 0.0
    discharge_kwh: float = 0.0
    roundtrip_efficiency: float = 0.0
    actual_utilization: float = 0.0


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


@dataclass(slots=True)
class UserSidePVExportParams:
    allow_export: bool = True
    sell_price: float = 0.0
    export_limit: float | None = None
    curtailment_cost_rate: float = 0.0


@dataclass(slots=True)
class UserSideDispatchPolicy:
    charge_allowed_hours: list[int] | None = None
    discharge_allowed_hours: list[int] | None = None
    pv_to_storage_reward_rate: float = 0.0
    pv_to_load_reward_rate: float = 0.0
    pv_export_penalty_rate: float = 0.0


@dataclass(slots=True)
class UserSidePVDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    pv_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    export: UserSidePVExportParams
    demand_charge_rate: float
    step_hours: float


@dataclass(slots=True)
class UserSidePVDispatchResult:
    pv_to_load: list[float]
    pv_to_grid: list[float]
    pv_curtailment: list[float]
    grid_import: list[float]
    max_grid_import: float
    energy_cost: float
    demand_cost: float
    sell_revenue: float
    curtailment_cost: float
    total_cost: float
    constraint_violations: dict[str, float]


@dataclass(slots=True)
class UserSidePVStorageDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    pv_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    export: UserSidePVExportParams
    demand_charge_rate: float
    step_hours: float
    storage: UserSideStorageParams
    initial_soc: float
    terminal_soc_target: float | None = None
    cycle_cost_rate: float = 0.0
    policy: UserSideDispatchPolicy | None = None


@dataclass(slots=True)
class UserSidePVStorageDispatchResult:
    pv_to_load: list[float]
    pv_to_storage: list[float]
    pv_to_grid: list[float]
    pv_curtailment: list[float]
    grid_to_load: list[float]
    grid_to_storage: list[float]
    charge_power: list[float]
    discharge_power: list[float]
    net_storage_power: list[float]
    soc: list[float]
    grid_import: list[float]
    max_grid_import: float
    energy_cost: float
    demand_cost: float
    sell_revenue: float
    curtailment_cost: float
    cycle_cost: float
    total_cost: float
    constraint_violations: dict[str, float]
