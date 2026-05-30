from .interfaces import (
    CvxpBESSDispatchInput,
    CvxpBESSDispatchResult,
    CvxpBESSProfile,
    MPCStepResult,
    BESSArbitrageResult,
    UserSideDispatchPolicy,
    UserSidePVDispatchInput,
    UserSidePVDispatchResult,
    UserSidePVExportParams,
    UserSidePVBESSDispatchInput,
    UserSidePVBESSDispatchResult,
    UserSideBESSDispatchInput,
    UserSideBESSDispatchResult,
    UserSideBESSParams,
)
from .mpc_bess import run_bess_mpc, solve_one_mpc_window
from .bess_arbitrage import solve_bess_arbitrage
from .two_stage_cvar import build_two_stage_cvar_model
from .user_side_pv_dispatch import run_user_side_pv_dispatch
from .user_side_pv_bess_dispatch import run_user_side_pv_bess_dispatch
from .user_side_bess_dispatch import run_user_side_bess_dispatch
from .cvxp_bess_dispatch import CVXP_PROFILES, get_cvxp_profile, run_cvxp_bess_dispatch
from .interfaces import (
    CabinetEqualityMode,
    DistBESSConfig,
    DistBESSDispatchInput,
    DistBESSDispatchResult,
    DistBESSPipelineParams,
    DistBESSSchedulerConfig,
    GridImportFormula,
    SolverType,
    TransformerConfig,
    DIST_BESS_CABINET_CAPACITY_KWH,
    DIST_BESS_CABINET_POWER_KW,
    DIST_BESS_CONSTRAINT_TOLERANCE_KW,
)

# ── 向后兼容 re-export（已迁移至 capacity_planning）──────────────────────────
_MOVED_TO_CAPACITY_PLANNING = frozenset({
    "CapacitySizingResult", "solve_capacity_sizing",
    "BESSDistributionScheduler", "SimulationResult",
    "SYSTEMS", "PRESETS", "TRANSFORMERS", "TRANSFORMER_BY_NAME",
    "V1_PRESET", "V2_PRESET", "V3_PRESET", "V4_PRESET", "V5_PRESET",
    "get_preset", "run_dist_bess_dispatch", "run_systems",
    "run_capacity_search", "optimize_combo",
    "simulate_schedule", "simulate_all",
    "build_devices_info", "cabinet_groups",
    "calculate_system_max_cabinets", "calculate_system_power_limit",
    "combo_key", "full_grid_candidates",
    "group_cabinet_count", "group_equal_cabinet_violation_count",
    "is_combo_feasible", "load_inputs", "load_base_data",
    "with_chinese_output_columns",
})


def __getattr__(name: str):
    if name in _MOVED_TO_CAPACITY_PLANNING:
        from ele_trading import capacity_planning
        return getattr(capacity_planning, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
