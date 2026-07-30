"""PuLP 求解的 typed 状态边界。

把 PuLP 的整数状态码和异常统一收敛为 SolveStatus 枚举 + SolverResult
数据类：求解失败时不抛异常、不返回伪造的零计划，由上层显式判断状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pulp import (
    LpProblem,
    LpStatus,
    LpStatusInfeasible,
    LpStatusNotSolved,
    LpStatusOptimal,
    LpStatusUnbounded,
    PULP_CBC_CMD,
    value,
)


class SolveStatus(str, Enum):
    """求解结果状态。

    注：PuLP 原生状态码只有 optimal / infeasible / unbounded / not_solved，
    FEASIBLE 目前不会产生，仅为语义完整性保留。
    """

    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    NOT_SOLVED = "not_solved"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SolverResult:
    """一次求解的完整结果快照。"""

    status: SolveStatus        # 映射后的 typed 状态
    objective_value: float | None  # 目标值；非最优/可行时为 None
    raw_status: int | None     # PuLP 原始整数状态码（异常时为 None）
    solver_name: str           # 实际使用的求解器类名
    message: str               # 状态描述或异常信息


# 向后兼容别名：历史代码使用 SolveResult 名称
SolveResult = SolverResult


def _map_status(raw_status: int) -> SolveStatus:
    """把 PuLP 整数状态码映射为 SolveStatus 枚举。"""
    if raw_status == LpStatusOptimal:
        return SolveStatus.OPTIMAL
    if raw_status == LpStatusInfeasible:
        return SolveStatus.INFEASIBLE
    if raw_status == LpStatusUnbounded:
        return SolveStatus.UNBOUNDED
    if raw_status == LpStatusNotSolved:
        return SolveStatus.NOT_SOLVED
    # 未知状态码一律按未求解处理，不猜测语义
    return SolveStatus.NOT_SOLVED


def solve_pulp_model(
    model: LpProblem,
    *,
    solver=None,
    msg: bool = False,
) -> SolverResult:
    """通过 PuLP 求解并返回状态，不抛异常、不返回伪造值。

    参数：
        model: 待求解的 PuLP 模型。
        solver: 可选求解器；缺省使用 CBC（PULP_CBC_CMD）。
        msg: 是否输出求解器日志。
    """
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    selected_solver = solver or PULP_CBC_CMD(msg=msg)
    solver_name = selected_solver.__class__.__name__
    try:
        raw_status = int(model.solve(selected_solver))
    except Exception as exc:
        # 求解器进程级失败（如二进制缺失）：返回 ERROR 而不是抛出
        return SolverResult(
            status=SolveStatus.ERROR,
            objective_value=None,
            raw_status=None,
            solver_name=solver_name,
            message=str(exc),
        )
    status = _map_status(raw_status)
    # 只在最优/可行时提取目标值，其余状态保持 None，避免误用无意义数值
    objective_value = (
        float(value(model.objective))
        if status in {SolveStatus.OPTIMAL, SolveStatus.FEASIBLE}
        and value(model.objective) is not None
        else None
    )
    return SolverResult(
        status=status,
        objective_value=objective_value,
        raw_status=raw_status,
        solver_name=solver_name,
        message=str(LpStatus.get(raw_status, "Unknown")),
    )
