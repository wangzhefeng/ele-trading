from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, List


@dataclass(slots=True)
class BESSArbitrageResult:
    objective: float
    p_ch: List[float]
    p_dis: List[float]
    soc: List[float]


# CapacitySizingResult 已迁移至 capacity_planning.bess_capacity_sizer
# 向后兼容由 optimization/__init__.__getattr__ 提供


@dataclass(slots=True)
class MPCStepResult:
    step: int
    price: float
    p_ch: float
    p_dis: float
    soc_next: float
    step_objective: float


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
class UserSideBESSDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    bess: UserSideBESSParams
    initial_soc: float
    demand_charge_rate: float
    step_hours: float
    terminal_soc_target: float | None = None
    cycle_cost_rate: float = 0.0


@dataclass(slots=True)
class UserSideBESSDispatchResult:
    charge_power: list[float]
    discharge_power: list[float]
    net_bess_power: list[float]
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
    pv_to_bess_reward_rate: float = 0.0
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
class UserSidePVBESSDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    pv_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    export: UserSidePVExportParams
    demand_charge_rate: float
    step_hours: float
    bess: UserSideBESSParams
    initial_soc: float
    terminal_soc_target: float | None = None
    cycle_cost_rate: float = 0.0
    policy: UserSideDispatchPolicy | None = None


@dataclass(slots=True)
class UserSidePVBESSDispatchResult:
    pv_to_load: list[float]
    pv_to_bess: list[float]
    pv_to_grid: list[float]
    pv_curtailment: list[float]
    grid_to_load: list[float]
    grid_to_bess: list[float]
    charge_power: list[float]
    discharge_power: list[float]
    net_bess_power: list[float]
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


# ── 分布式储能测算 (Dist BESS) ────────────────────────────────────────────────

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
