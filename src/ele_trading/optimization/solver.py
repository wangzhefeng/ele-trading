"""Typed PuLP solver status boundary."""

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
    OPTIMAL = "optimal"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    NOT_SOLVED = "not_solved"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SolverResult:
    status: SolveStatus
    objective_value: float | None
    raw_status: int | None
    solver_name: str
    message: str


SolveResult = SolverResult


def _map_status(raw_status: int) -> SolveStatus:
    if raw_status == LpStatusOptimal:
        return SolveStatus.OPTIMAL
    if raw_status == LpStatusInfeasible:
        return SolveStatus.INFEASIBLE
    if raw_status == LpStatusUnbounded:
        return SolveStatus.UNBOUNDED
    if raw_status == LpStatusNotSolved:
        return SolveStatus.NOT_SOLVED
    return SolveStatus.NOT_SOLVED


def solve_pulp_model(
    model: LpProblem,
    *,
    solver=None,
    msg: bool = False,
) -> SolverResult:
    """Solve through PuLP and return status without raising or fake values."""
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    selected_solver = solver or PULP_CBC_CMD(msg=msg)
    solver_name = selected_solver.__class__.__name__
    try:
        raw_status = int(model.solve(selected_solver))
    except Exception as exc:
        return SolverResult(
            status=SolveStatus.ERROR,
            objective_value=None,
            raw_status=None,
            solver_name=solver_name,
            message=str(exc),
        )
    status = _map_status(raw_status)
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

