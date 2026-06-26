"""
调度优化模块的公共数据类型。

定义所有调度优化算法共用的输入/输出 dataclass 和枚举。
"""

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
    # 现货逐时段售电价；提供（非 None）则优先于 sell_price，否则回退到 sell_price 标量。
    sell_price_list: list[float] | None = None
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
class UserSideRenewableDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    renewable_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    export: UserSidePVExportParams
    demand_charge_rate: float
    step_hours: float


@dataclass(slots=True)
class UserSideRenewableDispatchResult:
    renewable_to_load: list[float]
    renewable_to_grid: list[float]
    renewable_curtailment: list[float]
    grid_import: list[float]
    max_grid_import: float
    energy_cost: float
    demand_cost: float
    sell_revenue: float
    curtailment_cost: float
    total_cost: float
    constraint_violations: dict[str, float]


@dataclass(slots=True)
class UserSideRenewableBESSDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    renewable_forecast: list[float]
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
class UserSideRenewableBESSDispatchResult:
    renewable_to_load: list[float]
    renewable_to_bess: list[float]
    renewable_to_grid: list[float]
    renewable_curtailment: list[float]
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
class UserSideWindDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    wind_forecast: list[float]
    buy_price: list[float]
    price_type: list[str]
    export: UserSidePVExportParams
    demand_charge_rate: float
    step_hours: float


@dataclass(slots=True)
class UserSideWindDispatchResult:
    wind_to_load: list[float]
    wind_to_grid: list[float]
    wind_curtailment: list[float]
    grid_import: list[float]
    max_grid_import: float
    energy_cost: float
    demand_cost: float
    sell_revenue: float
    curtailment_cost: float
    total_cost: float
    constraint_violations: dict[str, float]


@dataclass(slots=True)
class UserSideWindBESSDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    wind_forecast: list[float]
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
class UserSideWindBESSDispatchResult:
    wind_to_load: list[float]
    wind_to_bess: list[float]
    wind_to_grid: list[float]
    wind_curtailment: list[float]
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
class UserSideWindPVBESSDispatchInput:
    timestamps: list[Any]
    load_forecast: list[float]
    pv_forecast: list[float]
    wind_forecast: list[float]
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
class UserSideWindPVBESSDispatchResult:
    pv_forecast: list[float]
    wind_forecast: list[float]
    renewable_forecast: list[float]
    renewable_to_load: list[float]
    renewable_to_bess: list[float]
    renewable_to_grid: list[float]
    renewable_curtailment: list[float]
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
    """
    CVXPY 调度算法的行为配置。
    """
    objective_energy_multiplier: float = 1.0
    demand_charge_type: str = "exact_max_net"
    smoothing_enabled: bool = False
    transformer_capacity_constraint: bool = False
    demand_peak_guard_constraint: bool = False


@dataclass(slots=True)
class CvxpBESSDispatchInput:
    """
    CVXPY 凸优化单节点储能调度输入。
    """
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
    """
    CVXPY 凸优化单节点储能调度输出。
    """
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
