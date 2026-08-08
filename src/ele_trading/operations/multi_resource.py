"""多资源联合优化：BESS 群 + 可调负荷/DR + 新能源限电 + 电网购电（v5 §11.1）。

单一时段能量平衡（MWh，禁止上网）：

    load + Σp_ch·dt + Σshift_up·dt
        = Σp_dis·dt + Σrenewable_used·dt + Σshift_down·dt + grid_import

目标：期望成本（能量 + 退化 + DR + 限电）+ cvar_weight × CVaR（可选
场景价格）。CVaR 复用 ``optimization.risk`` 的 Rockafellar-Uryasev
线性化；求解走统一 adapter ``optimization.solver``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
from pulp import LpMinimize, LpProblem, LpVariable, lpSum, value

from ele_trading.optimization.risk import add_cvar_auxiliaries
from ele_trading.optimization.solver import (
    SolveStatus,
    SolverResult,
    solve_pulp_model,
)


# ------------------------------------------------------------------ #
#  资源契约
# ------------------------------------------------------------------ #

def _var_tag(name: str) -> str:
    """LP 变量名只允许字母数字下划线，资源名中的连字符等必须转写。"""
    import re as _re

    return _re.sub(r"[^0-9A-Za-z_]", "_", name)


def _non_negative(value: float, field_name: str) -> float:
    result = float(value)
    if not np.isfinite(result) or result < 0.0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return result


@dataclass(frozen=True, slots=True)
class BESSUnit:
    """单台 BESS 的物理与经济参数。"""

    name: str
    soc0: float
    soc_min: float
    soc_max: float
    p_charge_max: float
    p_discharge_max: float
    eta_charge: float
    eta_discharge: float
    degradation_cost_per_mwh: float = 0.0
    terminal_soc_min: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("BESSUnit.name must not be empty")
        for field_name in (
            "soc0", "soc_min", "soc_max",
            "p_charge_max", "p_discharge_max",
            "degradation_cost_per_mwh",
        ):
            _non_negative(getattr(self, field_name), field_name)
        if not 0.0 < self.eta_charge <= 1.0 or not 0.0 < self.eta_discharge <= 1.0:
            raise ValueError("efficiencies must be within (0, 1]")
        if not self.soc_min <= self.soc0 <= self.soc_max:
            raise ValueError("soc0 must be within [soc_min, soc_max]")
        if self.soc_min >= self.soc_max:
            raise ValueError("soc_min must be below soc_max")
        if self.terminal_soc_min is not None:
            _non_negative(self.terminal_soc_min, "terminal_soc_min")


@dataclass(frozen=True, slots=True)
class DemandResponseUnit:
    """可调负荷：窗口内能量中性的上/下调节。"""

    name: str
    max_shift_down_mw: float
    max_shift_up_mw: float
    cost_per_mwh: float
    window: tuple[int, int]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("DemandResponseUnit.name must not be empty")
        _non_negative(self.max_shift_down_mw, "max_shift_down_mw")
        _non_negative(self.max_shift_up_mw, "max_shift_up_mw")
        _non_negative(self.cost_per_mwh, "cost_per_mwh")
        start, end = self.window
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            raise ValueError("window must be (start, end) with end > start")


@dataclass(frozen=True, slots=True)
class RenewableUnit:
    """新能源：可用出力外生，usage ≤ available，限电按成本惩罚。"""

    name: str
    available_mw: np.ndarray
    curtailment_cost_per_mwh: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("RenewableUnit.name must not be empty")
        available = np.asarray(self.available_mw, dtype=float)
        if available.ndim != 1 or not len(available) or not np.isfinite(available).all():
            raise ValueError("available_mw must be a finite 1-D vector")
        if (available < 0.0).any():
            raise ValueError("available_mw must be non-negative")
        object.__setattr__(self, "available_mw", available)
        _non_negative(self.curtailment_cost_per_mwh, "curtailment_cost_per_mwh")


@dataclass(frozen=True, slots=True)
class MultiResourcePortfolio:
    """编排器可选消费的资源组合，不改变单 BESS 默认路径。"""

    bess_units: tuple[BESSUnit, ...] = ()
    dr_units: tuple[DemandResponseUnit, ...] = ()
    renewable_units: tuple[RenewableUnit, ...] = ()

    def __post_init__(self) -> None:
        if not (self.bess_units or self.dr_units or self.renewable_units):
            raise ValueError("multi-resource portfolio must contain a resource")


# ------------------------------------------------------------------ #
#  结果契约
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class MultiResourceResult:
    """多资源联合优化结果。"""

    resource_schedules: Mapping[str, Mapping[str, list[float]]]
    dr_schedules: Mapping[str, Mapping[str, list[float]]]
    renewable_schedules: Mapping[str, Mapping[str, list[float]]]
    grid_import_mwh: np.ndarray | None
    expected_cost: float | None
    solve_result: SolverResult
    scenario_costs: Mapping[str, float] = field(default_factory=dict)
    cvar: float | None = None


# ------------------------------------------------------------------ #
#  求解
# ------------------------------------------------------------------ #

def _value(expression) -> float:
    result = value(expression)
    if result is None:
        raise RuntimeError("solver returned no value for a solved expression")
    return float(result)


def solve_multi_resource(
    *,
    load_mwh: np.ndarray,
    price: np.ndarray,
    bess_units: tuple[BESSUnit, ...] = (),
    dr_units: tuple[DemandResponseUnit, ...] = (),
    renewable_units: tuple[RenewableUnit, ...] = (),
    dt: float,
    scenario_prices: Mapping[str, np.ndarray] | None = None,
    scenario_probabilities: Mapping[str, float] | None = None,
    cvar_weight: float = 0.0,
    cvar_alpha: float = 0.95,
    solver=None,
) -> MultiResourceResult:
    """联合优化 BESS 群、DR 与新能源限电，最小化期望成本 + CVaR。"""
    load = np.asarray(load_mwh, dtype=float)
    prices = np.asarray(price, dtype=float)
    if (
        load.ndim != 1
        or prices.shape != load.shape
        or not len(load)
        or not np.isfinite(load).all()
        or not np.isfinite(prices).all()
    ):
        raise ValueError("load_mwh and price must be aligned finite vectors")
    if not np.isfinite(dt) or dt <= 0.0:
        raise ValueError("dt must be positive")
    horizon = len(load)
    steps = tuple(range(horizon))

    names = (
        [unit.name for unit in bess_units]
        + [unit.name for unit in dr_units]
        + [unit.name for unit in renewable_units]
    )
    if len(names) != len(set(names)):
        raise ValueError("duplicate resource names are not allowed")
    for unit in renewable_units:
        if len(unit.available_mw) != horizon:
            raise ValueError(
                f"available_mw of {unit.name!r} must match horizon"
            )
    for unit in dr_units:
        if unit.window[0] < 0 or unit.window[1] > horizon:
            raise ValueError(f"window of {unit.name!r} must be within horizon")

    scenario_cost_vectors: dict[str, np.ndarray] | None = None
    probabilities: dict[str, float] | None = None
    if scenario_prices is not None:
        if scenario_probabilities is None:
            raise ValueError(
                "scenario_probabilities are required with scenario_prices"
            )
        scenario_cost_vectors = {}
        for scenario_id, scenario_price in scenario_prices.items():
            vector = np.asarray(scenario_price, dtype=float)
            if vector.shape != load.shape or not np.isfinite(vector).all():
                raise ValueError(
                    f"scenario price {scenario_id!r} must match horizon"
                )
            scenario_cost_vectors[scenario_id] = vector
        if set(scenario_probabilities) != set(scenario_cost_vectors):
            raise ValueError("probability keys must match scenario keys")
        total_probability = float(sum(scenario_probabilities.values()))
        if not np.isclose(total_probability, 1.0, atol=1e-9):
            raise ValueError("scenario probability weights must sum to 1")
        probabilities = {
            key: float(weight) for key, weight in scenario_probabilities.items()
        }
    if not np.isfinite(cvar_weight) or cvar_weight < 0.0:
        raise ValueError("cvar_weight must be finite and non-negative")

    model = LpProblem("multi_resource", LpMinimize)

    # ---------------- BESS 群 ----------------
    charge: dict[str, dict[int, LpVariable]] = {}
    discharge: dict[str, dict[int, LpVariable]] = {}
    soc: dict[str, dict[int, LpVariable]] = {}
    for unit in bess_units:
        charge[unit.name] = {
            step: LpVariable(
                f"ch_{_var_tag(unit.name)}_{step}", lowBound=0.0, upBound=unit.p_charge_max
            )
            for step in steps
        }
        discharge[unit.name] = {
            step: LpVariable(
                f"dis_{_var_tag(unit.name)}_{step}",
                lowBound=0.0,
                upBound=unit.p_discharge_max,
            )
            for step in steps
        }
        soc[unit.name] = {
            step: LpVariable(
                f"soc_{_var_tag(unit.name)}_{step}",
                lowBound=unit.soc_min,
                upBound=unit.soc_max,
            )
            for step in steps
        }
        for step in steps:
            previous = unit.soc0 if step == 0 else soc[unit.name][step - 1]
            model += (
                soc[unit.name][step]
                == previous
                + unit.eta_charge * charge[unit.name][step] * dt
                - discharge[unit.name][step] * dt / unit.eta_discharge,
                f"soc_dyn_{_var_tag(unit.name)}_{step}",
            )
        if unit.terminal_soc_min is not None:
            model += (
                soc[unit.name][steps[-1]] >= unit.terminal_soc_min,
                f"soc_terminal_{_var_tag(unit.name)}",
            )

    # ---------------- DR ----------------
    shift_up: dict[str, dict[int, LpVariable]] = {}
    shift_down: dict[str, dict[int, LpVariable]] = {}
    for unit in dr_units:
        start, end = unit.window
        window_steps = tuple(range(start, end))
        shift_up[unit.name] = {
            step: LpVariable(
                f"drup_{_var_tag(unit.name)}_{step}", lowBound=0.0, upBound=unit.max_shift_up_mw
            )
            for step in window_steps
        }
        shift_down[unit.name] = {
            step: LpVariable(
                f"drdn_{_var_tag(unit.name)}_{step}",
                lowBound=0.0,
                upBound=unit.max_shift_down_mw,
            )
            for step in window_steps
        }
        # 窗口内能量中性：回补能量 = 削减能量
        model += (
            lpSum(shift_up[unit.name][step] for step in window_steps)
            == lpSum(shift_down[unit.name][step] for step in window_steps),
            f"dr_neutral_{_var_tag(unit.name)}",
        )

    # ---------------- 新能源 ----------------
    renewable_used: dict[str, dict[int, LpVariable]] = {}
    for unit in renewable_units:
        renewable_used[unit.name] = {
            step: LpVariable(
                f"ren_{_var_tag(unit.name)}_{step}",
                lowBound=0.0,
                upBound=float(unit.available_mw[step]),
            )
            for step in steps
        }

    # ---------------- 平衡与购电 ----------------
    grid_import: dict[int, LpVariable] = {
        step: LpVariable(f"grid_{step}", lowBound=0.0) for step in steps
    }
    for step in steps:
        model += (
            grid_import[step]
            == load[step]
            + lpSum(charge[unit.name][step] * dt for unit in bess_units)
            + lpSum(
                shift_up[unit.name][step] * dt
                for unit in dr_units
                if step in shift_up[unit.name]
            )
            - lpSum(discharge[unit.name][step] * dt for unit in bess_units)
            - lpSum(
                renewable_used[unit.name][step] * dt for unit in renewable_units
            )
            - lpSum(
                shift_down[unit.name][step] * dt
                for unit in dr_units
                if step in shift_down[unit.name]
            ),
            f"balance_{step}",
        )

    # ---------------- 非能量价格成本项 ----------------
    degradation_cost = lpSum(
        unit.degradation_cost_per_mwh
        * (charge[unit.name][step] + discharge[unit.name][step])
        * dt
        for unit in bess_units
        for step in steps
    )
    dr_cost = lpSum(
        unit.cost_per_mwh
        * (shift_up[unit.name][step] + shift_down[unit.name][step])
        * dt
        for unit in dr_units
        for step in shift_up[unit.name]
    )
    curtailment_cost = lpSum(
        unit.curtailment_cost_per_mwh
        * (float(unit.available_mw[step]) - renewable_used[unit.name][step])
        * dt
        for unit in renewable_units
        for step in steps
    )
    fixed_cost = degradation_cost + dr_cost + curtailment_cost

    # ---------------- 目标：期望 + CVaR ----------------
    cvar_value: float | None = None
    if scenario_cost_vectors is None:
        energy_cost = lpSum(
            grid_import[step] * float(prices[step]) for step in steps
        )
        model += energy_cost + fixed_cost
    else:
        assert probabilities is not None
        scenario_costs_expr = {
            scenario_id: lpSum(
                grid_import[step] * float(vector[step]) for step in steps
            )
            + fixed_cost
            for scenario_id, vector in scenario_cost_vectors.items()
        }
        expected = lpSum(
            probabilities[scenario_id] * scenario_costs_expr[scenario_id]
            for scenario_id in scenario_costs_expr
        )
        objective = expected
        if cvar_weight > 0.0:
            cvar = add_cvar_auxiliaries(
                model,
                scenario_costs_expr,
                probabilities,
                alpha=cvar_alpha,
                prefix="mr_cvar",
            )
            objective = objective + cvar_weight * cvar.expression
        model += objective

    solve_result = solve_pulp_model(model, solver=solver)
    if solve_result.status not in {SolveStatus.OPTIMAL, SolveStatus.FEASIBLE}:
        return MultiResourceResult(
            resource_schedules={},
            dr_schedules={},
            renewable_schedules={},
            grid_import_mwh=None,
            expected_cost=None,
            solve_result=solve_result,
        )

    # ---------------- 结果抽取 ----------------
    scenario_costs: dict[str, float] = {}
    if scenario_cost_vectors is not None:
        for scenario_id, vector in scenario_cost_vectors.items():
            scenario_costs[scenario_id] = float(
                sum(
                    _value(grid_import[step]) * float(vector[step])
                    for step in steps
                )
                + _value(fixed_cost)
            )
        if cvar_weight > 0.0:
            from ele_trading.optimization.risk import weighted_var_cvar

            _, cvar_value = weighted_var_cvar(
                scenario_costs,
                probabilities or {},
                alpha=cvar_alpha,
            )

    expected_cost = float(
        sum(_value(grid_import[step]) * float(prices[step]) for step in steps)
        + _value(fixed_cost)
    )

    return MultiResourceResult(
        resource_schedules={
            unit.name: {
                "p_charge": [_value(charge[unit.name][step]) for step in steps],
                "p_discharge": [
                    _value(discharge[unit.name][step]) for step in steps
                ],
                "soc": [_value(soc[unit.name][step]) for step in steps],
            }
            for unit in bess_units
        },
        dr_schedules={
            unit.name: {
                "shift_up_mw": [
                    _value(shift_up[unit.name][step])
                    if step in shift_up[unit.name]
                    else 0.0
                    for step in steps
                ],
                "shift_down_mw": [
                    _value(shift_down[unit.name][step])
                    if step in shift_down[unit.name]
                    else 0.0
                    for step in steps
                ],
            }
            for unit in dr_units
        },
        renewable_schedules={
            unit.name: {
                "used_mw": [
                    _value(renewable_used[unit.name][step]) for step in steps
                ],
                "curtailed_mw": [
                    float(unit.available_mw[step])
                    - _value(renewable_used[unit.name][step])
                    for step in steps
                ],
            }
            for unit in renewable_units
        },
        grid_import_mwh=np.asarray(
            [_value(grid_import[step]) for step in steps], dtype=float
        ),
        expected_cost=expected_cost,
        solve_result=solve_result,
        scenario_costs=scenario_costs,
        cvar=cvar_value,
    )
