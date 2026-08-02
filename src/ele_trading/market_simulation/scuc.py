"""安全约束机组组合（SCUC）与后定价（v5 §9.4/§9.5）。

MILP：开停机二元变量 + 启停/空载成本 + 最小开停机时间 + 多时段爬坡。
定价不走 MILP 对偶（整数模型对偶不可解释为 LMP），按 v5 §9.5：

1. 求解 SCUC 得到 commitment；
2. 固定整数开停机状态；
3. 重跑连续多时段 SCED；
4. 节点平衡对偶即能量 LMP；
5. 启停/空载导致的 uplift 单独核算，不混入 LMP。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from pulp import LpMinimize, LpProblem, LpVariable, lpSum, value

from ele_trading.optimization.solver import solve_pulp_model

from .grid.contracts import GridSnapshot
from .sced import DEFAULT_VOLL, SCEDResult, solve_sced_multiperiod


@dataclass(frozen=True, slots=True)
class SCUCResult:
    """SCUC 出清结果：commitment、出力与成本分解。"""

    commitment: Mapping[str, tuple[bool, ...]]
    dispatch_mw: Mapping[str, tuple[float, ...]]
    load_shed_mw: Mapping[str, tuple[float, ...]]
    total_cost: float
    energy_cost: float
    startup_cost: float
    no_load_cost: float
    shed_cost: float
    grid_version: str


@dataclass(frozen=True, slots=True)
class UpliftReport:
    """按机组核算的能量收入相对报价成本缺口（make-whole 视角）。"""

    per_generator_shortfall: Mapping[str, float]
    total_shortfall: float
    energy_revenue: Mapping[str, float]
    as_offered_cost: Mapping[str, float]


def _value(expression) -> float:
    result = value(expression)
    return 0.0 if result is None else float(result)


def solve_scuc(
    grid: GridSnapshot,
    load_mw: Mapping[str, Sequence[float]],
    *,
    voll: float = DEFAULT_VOLL,
    solver=None,
) -> SCUCResult:
    """多时段 SCUC（DC 网络 + 单时段潮流约束逐时段施加）。"""
    if not isinstance(grid, GridSnapshot):
        raise ValueError("grid must be a GridSnapshot")
    if not load_mw:
        raise ValueError("load_mw must not be empty")
    for bus_id in load_mw:
        if bus_id not in grid.bus_ids:
            raise ValueError(f"load_mw references unknown bus {bus_id!r}")
    periods = len(next(iter(load_mw.values())))
    if periods <= 0 or any(len(series) != periods for series in load_mw.values()):
        raise ValueError("all load series must share a positive length")
    if not np.isfinite(voll) or voll <= 0.0:
        raise ValueError("voll must be finite and positive")

    buses = tuple(bus.bus_id for bus in grid.buses)
    in_service = tuple(branch for branch in grid.branches if branch.in_service)
    generators = grid.generators
    steps = tuple(range(periods))
    load = {
        bus_id: [float(load_mw.get(bus_id, [0.0] * periods)[t]) for t in steps]
        for bus_id in buses
    }

    model = LpProblem("scuc", LpMinimize)

    on = {
        g.generator_id: {
            t: LpVariable(f"on_{g.generator_id}_{t}", cat="Binary") for t in steps
        }
        for g in generators
    }
    start = {
        g.generator_id: {
            t: LpVariable(f"start_{g.generator_id}_{t}", cat="Binary")
            for t in steps
        }
        for g in generators
    }
    dispatch = {
        g.generator_id: {
            t: LpVariable(f"p_{g.generator_id}_{t}", lowBound=0.0) for t in steps
        }
        for g in generators
    }
    shed = {
        bus_id: {
            t: LpVariable(
                f"shed_{bus_id}_{t}", lowBound=0.0, upBound=load[bus_id][t]
            )
            for t in steps
        }
        for bus_id in buses
    }
    angle = {
        bus_id: {t: LpVariable(f"theta_{bus_id}_{t}") for t in steps}
        for bus_id in buses
    }

    for g in generators:
        gid = g.generator_id
        for t in steps:
            model += dispatch[gid][t] <= g.p_max_mw * on[gid][t]
            model += dispatch[gid][t] >= g.p_min_mw * on[gid][t]
            previous_on = (
                on[gid][t - 1] if t > 0 else (1 if g.initial_on else 0)
            )
            model += start[gid][t] >= on[gid][t] - previous_on
            if t > 0:
                if g.ramp_up_mw > 0.0:
                    model += (
                        dispatch[gid][t] - dispatch[gid][t - 1]
                        <= g.ramp_up_mw,
                    )
                if g.ramp_down_mw > 0.0:
                    model += (
                        dispatch[gid][t - 1] - dispatch[gid][t]
                        <= g.ramp_down_mw,
                    )
            # 最小开机时间
            if g.minimum_up_periods > 0 and t + 1 < periods:
                window = tuple(
                    range(t + 1, min(periods, t + 1 + g.minimum_up_periods - 1))
                )
                for tau in window:
                    model += on[gid][tau] >= start[gid][t]

    reference = buses[0]
    for t in steps:
        model += angle[reference][t] == 0.0
        flows = {
            branch.branch_id: branch.susceptance
            * (angle[branch.from_bus][t] - angle[branch.to_bus][t])
            for branch in in_service
        }
        for branch in in_service:
            model += flows[branch.branch_id] <= branch.thermal_limit_mw
            model += flows[branch.branch_id] >= -branch.thermal_limit_mw
        for bus_id in buses:
            generation = lpSum(
                dispatch[g.generator_id][t]
                for g in generators
                if g.bus_id == bus_id
            )
            net_outflow = lpSum(
                flows[branch.branch_id]
                for branch in in_service
                if branch.from_bus == bus_id
            ) - lpSum(
                flows[branch.branch_id]
                for branch in in_service
                if branch.to_bus == bus_id
            )
            model += (
                generation + shed[bus_id][t] - net_outflow == load[bus_id][t]
            )

    energy_cost = lpSum(
        g.marginal_cost * dispatch[g.generator_id][t]
        for g in generators
        for t in steps
    )
    startup_cost = lpSum(
        g.startup_cost * start[g.generator_id][t] for g in generators for t in steps
    )
    no_load_cost = lpSum(
        g.no_load_cost * on[g.generator_id][t] for g in generators for t in steps
    )
    shed_cost = voll * lpSum(shed[bus_id][t] for bus_id in buses for t in steps)
    model += energy_cost + startup_cost + no_load_cost + shed_cost

    solve_pulp_model(model, solver=solver)

    return SCUCResult(
        commitment={
            g.generator_id: tuple(
                _value(on[g.generator_id][t]) > 0.5 for t in steps
            )
            for g in generators
        },
        dispatch_mw={
            g.generator_id: tuple(
                _value(dispatch[g.generator_id][t]) for t in steps
            )
            for g in generators
        },
        load_shed_mw={
            bus_id: tuple(_value(shed[bus_id][t]) for t in steps)
            for bus_id in buses
        },
        total_cost=_value(model.objective),
        energy_cost=_value(energy_cost),
        startup_cost=_value(startup_cost),
        no_load_cost=_value(no_load_cost),
        shed_cost=_value(shed_cost),
        grid_version=grid.version,
    )


def price_from_commitment(
    grid: GridSnapshot,
    load_mw: Mapping[str, Sequence[float]],
    commitment: Mapping[str, Sequence[bool]],
    *,
    voll: float = DEFAULT_VOLL,
    enforce_ramps: bool = True,
    solver=None,
) -> tuple[SCEDResult, ...]:
    """v5 §9.5 步骤 2-4：固定 commitment 后重跑连续多时段 SCED 取 LMP。"""
    unknown = set(commitment) - set(grid.generator_ids)
    if unknown:
        raise ValueError(
            "commitment references unknown generators: "
            + ", ".join(sorted(unknown))
        )
    return solve_sced_multiperiod(
        grid,
        load_mw,
        fixed_commitment=commitment,
        enforce_ramps=enforce_ramps,
        voll=voll,
        solver=solver,
    )


def compute_uplift(
    grid: GridSnapshot,
    sced_results: Sequence[SCEDResult],
    *,
    startup_cost: float = 0.0,
    no_load_cost: float = 0.0,
) -> UpliftReport:
    """能量收入 vs 报价成本缺口；启停/空载单独核算，不进 LMP。"""
    if not sced_results:
        raise ValueError("sced_results must not be empty")
    if not np.isfinite(startup_cost) or startup_cost < 0.0:
        raise ValueError("startup_cost must be finite and non-negative")
    if not np.isfinite(no_load_cost) or no_load_cost < 0.0:
        raise ValueError("no_load_cost must be finite and non-negative")

    revenue = {g.generator_id: 0.0 for g in grid.generators}
    offered = {g.generator_id: 0.0 for g in grid.generators}
    for result in sced_results:
        for g in grid.generators:
            dispatch = result.dispatch_mw[g.generator_id]
            revenue[g.generator_id] += result.lmp[g.bus_id] * dispatch
            offered[g.generator_id] += g.marginal_cost * dispatch
    periods = len(sced_results)
    for g in grid.generators:
        # 空载成本按并网时段分摊（以任一有出力或 p_min 约束时段近似）
        online_periods = sum(
            1
            for result in sced_results
            if result.dispatch_mw[g.generator_id] > 0.0 or g.p_min_mw > 0.0
        )
        offered[g.generator_id] += g.no_load_cost * min(online_periods, periods)
    shortfall = {
        gid: max(0.0, offered[gid] - revenue[gid]) for gid in offered
    }
    total_shortfall = float(sum(shortfall.values())) + float(startup_cost) + float(
        no_load_cost
    )
    return UpliftReport(
        per_generator_shortfall=shortfall,
        total_shortfall=total_shortfall,
        energy_revenue=revenue,
        as_offered_cost=offered,
    )
