"""分布式储能（多变压器、多储能柜）测算的输入输出与调度配置类型。

本模块为 capacity_planning 包内 dist_bess_dispatch 的数据契约，
从 optimization/interfaces.py 迁入，归属分布式储能测算域。
"""
from __future__ import annotations

from dataclasses import dataclass
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
