"""DC 安全约束经济调度（SCED）与节点边际价格（v5 §9.2/§9.3）。

模型（单时段 DC）：

.. math::

    \\min \\sum_g C_g p_g + VOLL \\sum_b s^{shed}_b
        + C_{curt} \\sum_b s^{curt}_b

    f_l = B_l(\\theta_{from} - \\theta_{to}),\\quad |f_l| \\le F_l^{max}

    \\sum_{g \\in b} p_g + r_b + s^{shed}_b - d_b
        = \\sum_{l: from=b} f_l - \\sum_{l: to=b} f_l

LMP 取节点功率平衡约束的对偶（PuLP/CBC ``constraint.pi``：最小化问题
中 RHS 增加 1 MW 负荷的成本增量）。DC 模型不表达无功、电压与 AC
可行性，节点级安全验证需另行离线复核（v5 §9.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from pulp import (
    LpAffineExpression,
    LpMinimize,
    LpProblem,
    LpVariable,
    lpSum,
    value,
)

from ele_trading.optimization.solver import SolveStatus, solve_pulp_model

from .grid.contracts import GridSnapshot

DEFAULT_VOLL = 10_000.0


@dataclass(frozen=True, slots=True)
class SCEDResult:
    """单时段 SCED 出清结果。"""

    dispatch_mw: Mapping[str, float]
    renewable_used_mw: Mapping[str, float]
    load_shed_mw: Mapping[str, float]
    branch_flows_mw: Mapping[str, float]
    bus_angles: Mapping[str, float]
    lmp: Mapping[str, float]
    total_cost: float
    energy_cost: float
    active_branch_ids: tuple[str, ...]
    reserve_shortfall_mw: float
    grid_version: str


def _value(expression) -> float:
    result = value(expression)
    return 0.0 if result is None else float(result)


def solve_sced(
    grid: GridSnapshot,
    load_mw: Mapping[str, float],
    renewable_available_mw: Mapping[str, float] | None = None,
    *,
    voll: float = DEFAULT_VOLL,
    curtailment_cost: float = 0.0,
    fixed_commitment: Mapping[str, bool] | None = None,
    ramp_from: Mapping[str, float] | None = None,
    solver=None,
) -> SCEDResult:
    """单时段 DC SCED。

    参数：
        fixed_commitment: SCUC 后定价时固定开停机；离网机组出力为 0，
            并网机组在 [p_min, p_max] 内连续调度（v5 §9.5 定价步骤 3）。
        ramp_from: 上时段出力；给出时对并网机组施加爬坡边界。
    """
    if not isinstance(grid, GridSnapshot):
        raise ValueError("grid must be a GridSnapshot")
    if not np.isfinite(voll) or voll <= 0.0:
        raise ValueError("voll must be finite and positive")
    if not np.isfinite(curtailment_cost) or curtailment_cost < 0.0:
        raise ValueError("curtailment_cost must be finite and non-negative")
    load = {bus.bus_id: float(load_mw.get(bus.bus_id, 0.0)) for bus in grid.buses}
    if any(not np.isfinite(amount) or amount < 0.0 for amount in load.values()):
        raise ValueError("load_mw must be finite and non-negative")
    renewable_available = {
        bus.bus_id: float((renewable_available_mw or {}).get(bus.bus_id, 0.0))
        for bus in grid.buses
    }
    if any(
        not np.isfinite(amount) or amount < 0.0
        for amount in renewable_available.values()
    ):
        raise ValueError("renewable_available_mw must be finite and non-negative")
    commitment = dict(fixed_commitment or {})
    unknown = set(commitment) - set(grid.generator_ids)
    if unknown:
        raise ValueError(
            "fixed_commitment references unknown generators: "
            + ", ".join(sorted(unknown))
        )
    if ramp_from is not None and set(ramp_from) - set(grid.generator_ids):
        raise ValueError("ramp_from references unknown generators")

    in_service_branches = tuple(
        branch for branch in grid.branches if branch.in_service
    )
    buses = tuple(bus.bus_id for bus in grid.buses)
    reference_bus = buses[0]

    model = LpProblem("sced", LpMinimize)

    # ---------------- 变量 ----------------
    dispatch = {}
    for generator in grid.generators:
        online = commitment.get(generator.generator_id, True)
        if not online:
            dispatch[generator.generator_id] = LpVariable(
                f"p_{generator.generator_id}", lowBound=0.0, upBound=0.0
            )
        else:
            lower = generator.p_min_mw if fixed_commitment is not None else 0.0
            # 纯 SCED（无 commitment）按常规经济调度处理：机组视为可用，
            # p_min 仅当显式固定 commitment 时施加（与后定价语义一致）
            dispatch[generator.generator_id] = LpVariable(
                f"p_{generator.generator_id}",
                lowBound=lower,
                upBound=generator.p_max_mw,
            )
    renewable_used = {
        bus_id: LpVariable(
            f"ren_{bus_id}", lowBound=0.0, upBound=renewable_available[bus_id]
        )
        for bus_id in buses
    }
    shed = {
        bus_id: LpVariable(f"shed_{bus_id}", lowBound=0.0, upBound=load[bus_id])
        for bus_id in buses
    }
    angle = {bus_id: LpVariable(f"theta_{bus_id}") for bus_id in buses}
    model += angle[reference_bus] == 0.0, "reference_angle"

    flows: dict[str, LpAffineExpression] = {}
    for branch in in_service_branches:
        flow = branch.susceptance * (
            angle[branch.from_bus] - angle[branch.to_bus]
        )
        flows[branch.branch_id] = flow
        model += flow <= branch.thermal_limit_mw, f"flow_max_{branch.branch_id}"
        model += flow >= -branch.thermal_limit_mw, f"flow_min_{branch.branch_id}"

    # ---------------- 爬坡（可选） ----------------
    if ramp_from is not None:
        for generator in grid.generators:
            if generator.generator_id not in ramp_from:
                continue
            online = commitment.get(generator.generator_id, True)
            if not online:
                continue
            previous = float(ramp_from[generator.generator_id])
            if generator.ramp_up_mw > 0.0:
                model += (
                    dispatch[generator.generator_id]
                    <= previous + generator.ramp_up_mw,
                    f"ramp_up_{generator.generator_id}",
                )
            if generator.ramp_down_mw > 0.0:
                model += (
                    dispatch[generator.generator_id]
                    >= previous - generator.ramp_down_mw,
                    f"ramp_down_{generator.generator_id}",
                )

    # ---------------- 节点平衡 ----------------
    balance_constraints = {}
    for bus_id in buses:
        generation = lpSum(
            dispatch[generator.generator_id]
            for generator in grid.generators
            if generator.bus_id == bus_id
        )
        net_outflow = lpSum(
            flows[branch.branch_id]
            for branch in in_service_branches
            if branch.from_bus == bus_id
        ) - lpSum(
            flows[branch.branch_id]
            for branch in in_service_branches
            if branch.to_bus == bus_id
        )
        constraint = (
            generation + renewable_used[bus_id] + shed[bus_id] - net_outflow
            == load[bus_id]
        )
        model += constraint, f"balance_{bus_id}"
        balance_constraints[bus_id] = constraint

    # ---------------- 备用（在线机组备用裕度） ----------------
    reserve_shortfall = LpVariable("reserve_shortfall", lowBound=0.0)
    if grid.reserve_requirement_mw > 0.0:
        headroom = lpSum(
            dispatch[generator.generator_id].upBound
            - dispatch[generator.generator_id]
            for generator in grid.generators
            if commitment.get(generator.generator_id, True)
        )
        model += (
            headroom + reserve_shortfall >= grid.reserve_requirement_mw,
            "reserve_requirement",
        )
    else:
        model += reserve_shortfall == 0.0, "reserve_trivial"

    # ---------------- 目标 ----------------
    energy_cost = lpSum(
        generator.marginal_cost * dispatch[generator.generator_id]
        for generator in grid.generators
    )
    shed_cost = voll * lpSum(shed[bus_id] for bus_id in buses)
    curtail_cost = curtailment_cost * lpSum(
        renewable_available[bus_id] - renewable_used[bus_id] for bus_id in buses
    )
    reserve_penalty = voll * reserve_shortfall
    model += energy_cost + shed_cost + curtail_cost + reserve_penalty

    solve_result = solve_pulp_model(model, solver=solver)
    if solve_result.status not in {SolveStatus.OPTIMAL, SolveStatus.FEASIBLE}:
        raise RuntimeError(
            "sced solve failed: "
            f"{solve_result.status.value}: {solve_result.message}"
        )

    # ---------------- 结果抽取 ----------------
    lmp: dict[str, float] = {}
    for bus_id in buses:
        pi = getattr(balance_constraints[bus_id], "pi", None)
        if pi is None:
            raise RuntimeError(
                "solver did not return balance duals; LMP unavailable"
            )
        lmp[bus_id] = float(pi)

    branch_flows = {
        branch.branch_id: _value(flows[branch.branch_id])
        for branch in in_service_branches
    }
    active_branches = tuple(
        branch.branch_id
        for branch in in_service_branches
        if abs(abs(branch_flows[branch.branch_id]) - branch.thermal_limit_mw)
        <= 1e-4
    )

    return SCEDResult(
        dispatch_mw={
            generator.generator_id: _value(dispatch[generator.generator_id])
            for generator in grid.generators
        },
        renewable_used_mw={
            bus_id: _value(renewable_used[bus_id]) for bus_id in buses
        },
        load_shed_mw={bus_id: _value(shed[bus_id]) for bus_id in buses},
        branch_flows_mw=branch_flows,
        bus_angles={bus_id: _value(angle[bus_id]) for bus_id in buses},
        lmp=lmp,
        total_cost=_value(model.objective),
        energy_cost=_value(energy_cost),
        active_branch_ids=active_branches,
        reserve_shortfall_mw=_value(reserve_shortfall),
        grid_version=grid.version,
    )


def solve_sced_multiperiod(
    grid: GridSnapshot,
    load_mw: Mapping[str, Sequence[float]],
    renewable_available_mw: Mapping[str, Sequence[float]] | None = None,
    *,
    voll: float = DEFAULT_VOLL,
    curtailment_cost: float = 0.0,
    fixed_commitment: Mapping[str, Sequence[bool]] | None = None,
    enforce_ramps: bool = True,
    initial_dispatch_mw: Mapping[str, float] | None = None,
    solver=None,
) -> tuple[SCEDResult, ...]:
    """多时段 SCED：逐时段求解，时段间以爬坡约束耦合。

    SCUC 后定价路径使用（v5 §9.5 步骤 3-4）：commitment 固定后各时段
    均为连续 LP；爬坡在 commitment 固定下保持线性。
    """
    periods = len(next(iter(load_mw.values()))) if load_mw else 0
    if periods <= 0:
        raise ValueError("load_mw must contain at least one period")
    for bus_id, series in load_mw.items():
        if bus_id not in grid.bus_ids:
            raise ValueError(f"load_mw references unknown bus {bus_id!r}")
        if len(series) != periods:
            raise ValueError("all load series must share the same length")
    renewable = renewable_available_mw or {}
    results: list[SCEDResult] = []
    previous_dispatch = dict(initial_dispatch_mw or {})
    for period in range(periods):
        commitment = (
            {
                generator_id: bool(flags[period])
                for generator_id, flags in fixed_commitment.items()
            }
            if fixed_commitment is not None
            else None
        )
        result = solve_sced(
            grid,
            {bus_id: float(series[period]) for bus_id, series in load_mw.items()},
            {
                bus_id: float(series[period])
                for bus_id, series in renewable.items()
            }
            if renewable
            else None,
            voll=voll,
            curtailment_cost=curtailment_cost,
            fixed_commitment=commitment,
            ramp_from=previous_dispatch if enforce_ramps else None,
            solver=solver,
        )
        results.append(result)
        previous_dispatch = dict(result.dispatch_mw)
    return tuple(results)
