from dataclasses import dataclass, field
from datetime import datetime
from typing import List


@dataclass
class AlgorithmProfile:
    """控制算法行为的配置参数。"""
    objective_energy_multiplier: float  # 目标函数能量项系数: 1.0 或 31.0
    demand_charge_type: str  # 需量电费类型: "none" / "approx_min_charge" / "exact_max_net"
    smoothing_enabled: bool  # 是否启用平滑惩罚
    transformer_capacity_constraint: bool  # 是否启用变压器容量约束
    demand_peak_guard_constraint: bool  # 是否启用需量峰值保护约束
    time_splitting: str  # 时间分割策略: "month" 或 "day"


WITHOUT_DEMAND_PROFILE = AlgorithmProfile(
    objective_energy_multiplier=1.0,
    demand_charge_type="none",
    smoothing_enabled=False,
    transformer_capacity_constraint=False,
    demand_peak_guard_constraint=True,
    time_splitting="month",
)

BASIC_PROFILE = AlgorithmProfile(
    objective_energy_multiplier=31.0,
    demand_charge_type="approx_min_charge",
    smoothing_enabled=True,
    transformer_capacity_constraint=False,
    demand_peak_guard_constraint=False,
    time_splitting="day",
)

OPTIM_PROFILE = AlgorithmProfile(
    objective_energy_multiplier=1.0,
    demand_charge_type="exact_max_net",
    smoothing_enabled=False,
    transformer_capacity_constraint=True,
    demand_peak_guard_constraint=False,
    time_splitting="month",
)

_PROFILES = {
    "without_demand": WITHOUT_DEMAND_PROFILE,
    "basic": BASIC_PROFILE,
    "optim": OPTIM_PROFILE,
}


def get_profile(version: str) -> AlgorithmProfile:
    """根据版本名称获取对应的算法配置。"""
    if version not in _PROFILES:
        raise ValueError(f"Unknown version: {version}. Choose from {list(_PROFILES.keys())}")
    return _PROFILES[version]


@dataclass
class PipelineParams:
    """实验流水线参数。"""
    exp_name: str
    start_time: datetime
    end_time: datetime
    freq_minutes: int
    es_scale_list: list
    node_name_list: list
    max_demand_price: float
    transform_capacity: float = 0.0
    num_processes: int = 8
    strategy_dir: str = ""
    current_soc: float = 0.0
