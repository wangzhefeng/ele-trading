from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


# ── 枚举 ──────────────────────────────────────────────────────────────────────


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


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TransformerConfig:
    name: str
    load_file: str
    transformer_capacity: float
    max_cabinets: int


@dataclass(frozen=True)
class SystemConfig:
    name: str
    transformers: tuple[TransformerConfig, ...]
    cabinet_groups: tuple[tuple[str, ...], ...] = ()
    park_load_file: str = "demand_load.csv"


@dataclass
class SchedulerConfig:
    solver: SolverType = SolverType.LP
    grid_import_formula: GridImportFormula = GridImportFormula.PARK_BASELINE
    grid_import_nonneg: bool = False
    discharge_mask_mode: str = "price_type"
    smooth_penalty_weight: float = 0.0
    ramp_rate_fraction_per_step: float | None = None
    charge_target_penalty_weight: float = 0.0
    discharge_target_penalty_weight: float = 0.0


@dataclass
class PipelineParams:
    start_time: datetime
    end_time: datetime
    max_demand_price: float
    freq_minutes: int = 15


# ── 拓扑常量 ──────────────────────────────────────────────────────────────────

CABINET_POWER_KW = 150.0
CABINET_CAPACITY_KWH = 300.0
CONSTRAINT_TOLERANCE_KW = 1e-2

TRANSFORMERS = [
    TransformerConfig("338_1", "demand_load_338_1.csv", 2000.0, 13),
    TransformerConfig("338_2", "demand_load_338_2.csv", 1600.0, 10),
    TransformerConfig("338_3", "demand_load_338_3.csv", 1600.0, 10),
    TransformerConfig("342_1", "demand_load_342_1.csv", 1250.0, 8),
    TransformerConfig("342_2", "demand_load_342_2.csv", 1250.0, 8),
]
TRANSFORMER_BY_NAME: dict[str, TransformerConfig] = {cfg.name: cfg for cfg in TRANSFORMERS}

SYSTEMS: dict[str, SystemConfig] = {
    "338": SystemConfig(
        "338",
        (
            TRANSFORMER_BY_NAME["338_1"],
            TRANSFORMER_BY_NAME["338_2"],
            TRANSFORMER_BY_NAME["338_3"],
        ),
    ),
    "342": SystemConfig(
        "342",
        (
            TRANSFORMER_BY_NAME["342_1"],
            TRANSFORMER_BY_NAME["342_2"],
        ),
    ),
    "park": SystemConfig(
        "park",
        (
            TRANSFORMER_BY_NAME["338_1"],
            TRANSFORMER_BY_NAME["338_2"],
            TRANSFORMER_BY_NAME["338_3"],
            TRANSFORMER_BY_NAME["342_1"],
            TRANSFORMER_BY_NAME["342_2"],
        ),
        cabinet_groups=(
            ("338_1", "338_2", "338_3"),
            ("342_1", "342_2"),
        ),
    ),
}

# ── 预设 ──────────────────────────────────────────────────────────────────────

V1_PRESET = SchedulerConfig(
    solver=SolverType.LP,
    grid_import_formula=GridImportFormula.SUM_LOAD,
    grid_import_nonneg=False,
    discharge_mask_mode="price_type",
    smooth_penalty_weight=0.0,
    ramp_rate_fraction_per_step=None,
    charge_target_penalty_weight=0.0,
    discharge_target_penalty_weight=0.0,
)

V2_PRESET = SchedulerConfig(
    solver=SolverType.LP,
    grid_import_formula=GridImportFormula.SUM_LOAD,
    grid_import_nonneg=False,
    discharge_mask_mode="price_type",
    smooth_penalty_weight=1e-4,
    ramp_rate_fraction_per_step=0.5,
    charge_target_penalty_weight=0.0,
    discharge_target_penalty_weight=0.0,
)

V3_PRESET = V2_PRESET

V4_PRESET = SchedulerConfig(
    solver=SolverType.LP,
    grid_import_formula=GridImportFormula.PARK_BASELINE,
    grid_import_nonneg=True,
    discharge_mask_mode="price_type",
    smooth_penalty_weight=1e-4,
    ramp_rate_fraction_per_step=0.5,
    charge_target_penalty_weight=0.0,
    discharge_target_penalty_weight=0.0,
)

V5_PRESET = SchedulerConfig(
    solver=SolverType.RULE_BASED,
    grid_import_formula=GridImportFormula.PARK_BASELINE,
    grid_import_nonneg=True,
    discharge_mask_mode="fixed_window",
    smooth_penalty_weight=0.0,
    ramp_rate_fraction_per_step=None,
    charge_target_penalty_weight=0.0,
    discharge_target_penalty_weight=0.0,
)

PRESETS: dict[str, SchedulerConfig] = {
    "v1": V1_PRESET,
    "v2": V2_PRESET,
    "v3": V3_PRESET,
    "v4": V4_PRESET,
    "v5": V5_PRESET,
}


def get_preset(name: str) -> SchedulerConfig:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: {name}. Choose from {list(PRESETS)}")
    return PRESETS[name]
