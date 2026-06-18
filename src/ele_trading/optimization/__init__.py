"""
调度优化模块

提供储能（BESS）和光伏（PV）调度优化算法及对应的输入/输出数据类型。
"""

from .interfaces import (
    BESSArbitrageResult,
    CabinetEqualityMode,
    CvxpBESSDispatchInput,
    CvxpBESSDispatchResult,
    CvxpBESSProfile,
    DIST_BESS_CABINET_CAPACITY_KWH,
    DIST_BESS_CABINET_POWER_KW,
    DIST_BESS_CONSTRAINT_TOLERANCE_KW,
    DistBESSConfig,
    DistBESSDispatchInput,
    DistBESSDispatchResult,
    DistBESSPipelineParams,
    DistBESSSchedulerConfig,
    GridImportFormula,
    MPCStepResult,
    SolverType,
    TransformerConfig,
    UserSideBESSDispatchInput,
    UserSideBESSDispatchResult,
    UserSideBESSParams,
    UserSideDispatchPolicy,
    UserSidePVBESSDispatchInput,
    UserSidePVBESSDispatchResult,
    UserSidePVDispatchInput,
    UserSidePVDispatchResult,
    UserSidePVExportParams,
    UserSideRenewableBESSDispatchInput,
    UserSideRenewableBESSDispatchResult,
    UserSideRenewableDispatchInput,
    UserSideRenewableDispatchResult,
    UserSideWindBESSDispatchInput,
    UserSideWindBESSDispatchResult,
    UserSideWindDispatchInput,
    UserSideWindDispatchResult,
    UserSideWindPVBESSDispatchInput,
    UserSideWindPVBESSDispatchResult,
)
from .bess_arbitrage import solve_bess_arbitrage
from .mpc_bess import run_bess_mpc, solve_one_mpc_window
from .two_stage_cvar import build_two_stage_cvar_model
from .user_side_bess_dispatch import run_user_side_bess_dispatch
from .user_side_pv_bess_dispatch import run_user_side_pv_bess_dispatch
from .user_side_pv_dispatch import run_user_side_pv_dispatch
from .user_side_renewable_bess_dispatch import run_user_side_renewable_bess_dispatch
from .user_side_renewable_dispatch import run_user_side_renewable_dispatch
from .user_side_wind_bess_dispatch import run_user_side_wind_bess_dispatch
from .user_side_wind_dispatch import run_user_side_wind_dispatch
from .user_side_wind_pv_bess_dispatch import run_user_side_wind_pv_bess_dispatch


# 延迟导入：cvxpy 是重型可选依赖，缺失时不应阻塞优化包的其他路径（PuLP / Pyomo）
def __getattr__(name):
    _DELAYED = {"CVXP_PROFILES", "get_cvxp_profile", "run_cvxp_bess_dispatch"}
    if name in _DELAYED:
        from . import user_side_bess_dispatch_cvxpy as _cvxp
        attr = getattr(_cvxp, name)
        # 缓存到模块命名空间，下次直接命中
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
