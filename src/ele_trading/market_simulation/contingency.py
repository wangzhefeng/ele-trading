"""N-1 安全校验：显式事故重调度（v5 §9.7）。

首版实施显式枚举：对每个在运支路事故重解 SCED，输出可行性、
负荷损失与成本/价格变化。PTDF/LODF 快速筛选与只对规则要求的
安全集计入正式出清属于后续阶段；本模块不做"伪真值"承诺。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .grid.contracts import Branch, GridSnapshot
from .sced import DEFAULT_VOLL, SCEDResult, solve_sced


@dataclass(frozen=True, slots=True)
class ContingencyOutcome:
    """单个 N-1 事故的重调度结果。"""

    contingency_id: str
    feasible: bool
    load_shed_mw: float
    total_cost: float
    cost_delta: float
    max_lmp: float


@dataclass(frozen=True, slots=True)
class N1Report:
    """全网 N-1 校验报告。"""

    base_case: SCEDResult
    outcomes: tuple[ContingencyOutcome, ...]

    @property
    def secure(self) -> bool:
        return all(outcome.feasible for outcome in self.outcomes)

    @property
    def worst_contingency_id(self) -> str | None:
        if not self.outcomes:
            return None
        worst = max(self.outcomes, key=lambda outcome: outcome.cost_delta)
        return worst.contingency_id


def run_n1_screening(
    grid: GridSnapshot,
    load_mw: Mapping[str, float],
    renewable_available_mw: Mapping[str, float] | None = None,
    *,
    voll: float = DEFAULT_VOLL,
    solver=None,
) -> N1Report:
    """对每个在运支路事故重解 SCED 并汇总安全结论。"""
    base = solve_sced(
        grid,
        load_mw,
        renewable_available_mw,
        voll=voll,
        solver=solver,
    )
    outcomes: list[ContingencyOutcome] = []
    for branch in grid.branches:
        if not branch.in_service:
            continue
        outage_branches = tuple(
            Branch(
                branch_id=item.branch_id,
                from_bus=item.from_bus,
                to_bus=item.to_bus,
                susceptance=item.susceptance,
                thermal_limit_mw=item.thermal_limit_mw,
                in_service=False if item.branch_id == branch.branch_id else item.in_service,
            )
            for item in grid.branches
        )
        outage_grid = GridSnapshot(
            as_of=grid.as_of,
            version=f"{grid.version}:n1-{branch.branch_id}",
            buses=grid.buses,
            branches=outage_branches,
            generators=grid.generators,
            reserve_requirement_mw=grid.reserve_requirement_mw,
        )
        try:
            result = solve_sced(
                outage_grid,
                load_mw,
                renewable_available_mw,
                voll=voll,
                solver=solver,
            )
            shed = float(sum(result.load_shed_mw.values()))
            outcomes.append(
                ContingencyOutcome(
                    contingency_id=branch.branch_id,
                    feasible=shed <= 1e-6,
                    load_shed_mw=shed,
                    total_cost=result.total_cost,
                    cost_delta=result.total_cost - base.total_cost,
                    max_lmp=float(max(result.lmp.values())),
                )
            )
        except RuntimeError:
            outcomes.append(
                ContingencyOutcome(
                    contingency_id=branch.branch_id,
                    feasible=False,
                    load_shed_mw=float("inf"),
                    total_cost=float("inf"),
                    cost_delta=float("inf"),
                    max_lmp=float("inf"),
                )
            )
    return N1Report(base_case=base, outcomes=tuple(outcomes))
