"""分布式储能（多变压器、多储能柜）测算的输入输出与调度配置类型。

本模块为 capacity_planning 包内 bess_capacity_distributed_planner 的数据契约，
从 optimization/interfaces.py 迁入，归属分布式储能测算域。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


DIST_BESS_CABINET_POWER_KW = 150.0
DIST_BESS_CABINET_CAPACITY_KWH = 300.0
DIST_BESS_CONSTRAINT_TOLERANCE_KW = 1e-2


class SolverType(Enum):
    LP = "lp"
    RULE_BASED = "rule"


class CabinetEqualityMode(Enum):
    NONE = "none"
    GLOBAL = "global"
    GROUP = "group"


class GridImportFormula(Enum):
    SUM_LOAD = "sum_load"
    PARK_BASELINE = "park"


@dataclass(frozen=True)
class TransformerConfig:
    name: str
    load_file: str
    transformer_capacity: float
    max_cabinets: int


@dataclass(frozen=True)
class DistBESSConfig:
    name: str
    transformers: tuple[TransformerConfig, ...]
    cabinet_groups: tuple[tuple[str, ...], ...] = ()
    park_load_file: str = "demand_load.csv"


@dataclass
class DistBESSSchedulerConfig:
    solver: SolverType = SolverType.LP
    grid_import_formula: GridImportFormula = GridImportFormula.PARK_BASELINE
    grid_import_nonneg: bool = False
    discharge_mask_mode: str = "price_type"
    demand_charge_mode: str = "point_max"
    demand_charge_window_minutes: int = 15
    smooth_penalty_weight: float = 0.0
    ramp_rate_fraction_per_step: float | None = None
    charge_target_penalty_weight: float = 0.0
    discharge_target_penalty_weight: float = 0.0


@dataclass
class DistBESSPipelineParams:
    """分布式储能测算流水线参数。"""
    start_time: datetime
    end_time: datetime
    max_demand_price: float = 33.8
    freq_minutes: int = 15


@dataclass(slots=True)
class DistBESSDispatchInput:
    """分布式储能容量搜索输入。"""
    base_dir: str
    start_time: datetime
    end_time: datetime
    max_demand_price: float = 33.8
    freq_minutes: int = 15
    preset: str = "v4"
    system_name: str = "park"
    search_mode: str = "coordinate"
    workers: int = 1
    min_cabinets_per_transformer: int = 1


@dataclass(slots=True)
class DistBESSDispatchResult:
    """分布式储能容量搜索输出。"""
    summary: Any
    output_dir: str
    preset: str
    system_name: str
    best_revenue: float = 0.0
    best_combo_key: str = ""
    best_total_cabinets: int = 0
    best_total_capacity_kwh: float = 0.0


@dataclass(slots=True)
class UserSideBESSParams:
    capacity: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float = 0.95
    eta_dis: float = 0.95


@dataclass(slots=True)
class CvxpBESSProfile:
    """CVXPY 调度算法的行为配置。"""
    objective_energy_multiplier: float = 1.0
    demand_charge_type: str = "exact_max_net"
    smoothing_enabled: bool = False
    transformer_capacity_constraint: bool = False
    demand_peak_guard_constraint: bool = False


@dataclass(slots=True)
class CvxpBESSDispatchInput:
    """CVXPY 凸优化单节点储能调度输入。"""
    timestamps: list[datetime]
    demand_load: list[float]
    ele_prices: list[float]
    ele_types: list[str]
    bess: UserSideBESSParams
    initial_soc: float = 0.0
    max_demand_price: float = 0.0
    freq_minutes: int = 60
    profile: CvxpBESSProfile = field(default_factory=CvxpBESSProfile)
    transform_capacity: float = 0.0


@dataclass(slots=True)
class CvxpBESSDispatchResult:
    """CVXPY 凸优化单节点储能调度输出。"""
    charge_power: list[float]
    discharge_power: list[float]
    net_power: list[float]
    soc: list[float]
    objective_value: float


@dataclass(slots=True)
class DistributedBESSNodeParams:
    name: str
    transformer_capacity_kw: float
    bess_power_kw: float
    bess_capacity_kwh: float
    soc_min_kwh: float
    soc_max_kwh: float
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95


@dataclass(slots=True)
class DistributedBESSDemandChargeConfig:
    mode: str = "point_max"
    window_minutes: int = 15


@dataclass(slots=True)
class DistributedBESSDispatchPolicy:
    charge_allowed_hours: list[int] | None = None
    discharge_allowed_hours: list[int] | None = None
    discharge_mask_mode: str = "price_type"
    cross_transformer_support: bool = True
    cross_flow_penalty_rate: float = 1e-6
    smooth_penalty_weight: float = 0.0
    ramp_rate_fraction_per_step: float | None = None
    charge_target_penalty_weight: float = 0.0
    discharge_target_penalty_weight: float = 0.0
    terminal_soc_target_kwh: list[float] | None = None
    terminal_soc_penalty_weight: float = 0.0


@dataclass(slots=True)
class DistributedBESSDispatchInput:
    timestamps: list[Any]
    local_load_forecast: list[list[float]]
    system_load_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    nodes: list[DistributedBESSNodeParams]
    initial_soc_kwh: list[float]
    step_hours: float
    demand_charge_rate: float
    grid_import_formula: str = "park_baseline"
    grid_import_nonneg: bool = False
    demand_charge: DistributedBESSDemandChargeConfig = field(
        default_factory=DistributedBESSDemandChargeConfig
    )
    policy: DistributedBESSDispatchPolicy | None = None
    solver: str = "lp"


@dataclass(slots=True)
class DistributedBESSDispatchResult:
    charge_power_by_node: list[list[float]]
    discharge_power_by_node: list[list[float]]
    net_bess_power_by_node: list[list[float]]
    soc_by_node: list[list[float]]
    grid_to_load_by_node: list[list[float]]
    grid_import_total: list[float]
    transformer_import_by_node: list[list[float]]
    transformer_export_by_node: list[list[float]]
    allocation_by_source_target: list[list[list[float]]]
    max_demand_kw: float
    energy_cost: float
    demand_cost: float
    cross_flow_cost: float
    smooth_cost: float
    soc_target_cost: float
    total_cost: float
    solver_status: str
    solver_name: str
    constraint_violations: dict[str, float]
