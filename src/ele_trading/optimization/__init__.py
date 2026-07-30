"""活动 v2 优化内核及其通用结果契约的统一导出口。

汇集市场侧储能优化的全部活动 API：
- 共享储能约束内核（bess_model）
- 确定性单市场套利（bess_arbitrage）
- MPC 滚动优化（mpc_bess）
- Two-stage + CVaR 随机优化（two_stage_cvar）
- CVaR 风险工具（risk）、typed 求解边界（solver）、结果契约（contracts）

归档的用户侧 / 分布式 / CVXPY 实现位于 todo/ 子目录，不在此导出。
"""

from .bess_model import (
    BESSConfig,
    BESSParameters,
    BESSVariables,
    add_bess_constraints,
)
from .bess_arbitrage import solve_bess_arbitrage
from .contracts import BESSArbitrageResult, MPCStepResult
from .mpc_bess import run_bess_mpc, solve_one_mpc_window
from .risk import (
    CVaRAuxiliaries,
    add_cvar_auxiliaries,
    weighted_var_cvar,
)
from .solver import SolveStatus, SolverResult, solve_pulp_model
from .two_stage_cvar import (
    ScenarioRecourse,
    TwoStageCVaRResult,
    build_two_stage_cvar_model,
    solve_two_stage_cvar,
)

__all__ = [
    "BESSArbitrageResult",
    "BESSConfig",
    "BESSParameters",
    "BESSVariables",
    "CVaRAuxiliaries",
    "MPCStepResult",
    "ScenarioRecourse",
    "SolveStatus",
    "SolverResult",
    "TwoStageCVaRResult",
    "add_bess_constraints",
    "add_cvar_auxiliaries",
    "build_two_stage_cvar_model",
    "run_bess_mpc",
    "solve_bess_arbitrage",
    "solve_one_mpc_window",
    "solve_pulp_model",
    "solve_two_stage_cvar",
    "weighted_var_cvar",
]
