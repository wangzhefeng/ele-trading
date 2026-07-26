from __future__ import annotations

import cvxpy as cp
import numpy as np

from .interfaces import (
    CvxpBESSDispatchInput,
    CvxpBESSDispatchResult,
    CvxpBESSProfile,
)


def _to_list(arr, tol: float = 1e-6) -> list[float]:
    if arr is None:
        return []
    values = []
    for x in np.asarray(arr).flatten():
        value = float(x)
        if abs(value) <= tol:
            value = 0.0
        values.append(round(value, 6))
    return values


CVXP_PROFILES: dict[str, CvxpBESSProfile] = {
    "without_demand": CvxpBESSProfile(
        objective_energy_multiplier=1.0,
        demand_charge_type="none",
        smoothing_enabled=False,
        transformer_capacity_constraint=False,
        demand_peak_guard_constraint=True,
    ),
    "basic": CvxpBESSProfile(
        objective_energy_multiplier=31.0,
        demand_charge_type="approx_min_charge",
        smoothing_enabled=True,
        transformer_capacity_constraint=False,
        demand_peak_guard_constraint=False,
    ),
    "optim": CvxpBESSProfile(
        objective_energy_multiplier=1.0,
        demand_charge_type="exact_max_net",
        smoothing_enabled=False,
        transformer_capacity_constraint=True,
        demand_peak_guard_constraint=False,
    ),
}


class CvxpBESSDispatcher:
    """CVXPY 单节点储能调度 class 内核。"""

    def __init__(self, dispatch_input: CvxpBESSDispatchInput) -> None:
        self.dispatch_input = dispatch_input

    def solve(self) -> CvxpBESSDispatchResult:
        self._validate_input()
        self._init_params()
        self._create_variables()
        self._build_objective()
        self._build_constraints()
        self._solve_problem()
        values = self._extract_solution()
        return self._build_result(values)

    def _validate_input(self) -> None:
        dispatch_input = self.dispatch_input
        length = len(dispatch_input.timestamps)
        if length == 0:
            raise ValueError("dispatch horizon must not be empty")
        if not (
            len(dispatch_input.demand_load)
            == len(dispatch_input.ele_prices)
            == len(dispatch_input.ele_types)
            == length
        ):
            raise ValueError(
                "timestamps, demand_load, ele_prices, and ele_types "
                "must have the same length"
            )
        if dispatch_input.freq_minutes <= 0:
            raise ValueError("freq_minutes must be positive")
        if dispatch_input.max_demand_price < 0:
            raise ValueError("max_demand_price must be non-negative")
        if any(load < 0 for load in dispatch_input.demand_load):
            raise ValueError("demand_load must be non-negative")

        bess = dispatch_input.bess
        if bess.capacity <= 0:
            raise ValueError("bess.capacity must be positive")
        if bess.soc_min < 0 or bess.soc_max > bess.capacity:
            raise ValueError("bess SOC bounds must be within bess capacity")
        if bess.soc_min > bess.soc_max:
            raise ValueError("bess.soc_min must be less than or equal to bess.soc_max")
        if not bess.soc_min <= dispatch_input.initial_soc <= bess.soc_max:
            raise ValueError("initial_soc must be within bess SOC bounds")
        if bess.p_ch_max < 0 or bess.p_dis_max < 0:
            raise ValueError("bess power limits must be non-negative")
        if bess.eta_ch <= 0 or bess.eta_dis <= 0:
            raise ValueError("bess efficiencies must be positive")
        if (
            dispatch_input.profile.transformer_capacity_constraint
            and dispatch_input.transform_capacity <= 0
        ):
            raise ValueError(
                "transform_capacity must be positive when transformer_capacity_constraint is enabled"
            )

    def _init_params(self) -> None:
        bess = self.dispatch_input.bess
        self.T = len(self.dispatch_input.timestamps)
        self.eta_ch = bess.eta_ch
        self.eta_dis = bess.eta_dis
        self.p_ch_max = bess.p_ch_max
        self.p_dis_max = bess.p_dis_max
        self.soc_max = bess.soc_max
        self.soc_min = bess.soc_min
        self.time_ratio = self.dispatch_input.freq_minutes / 60
        self.demand = np.array(self.dispatch_input.demand_load, dtype=float)
        self.price = np.array(self.dispatch_input.ele_prices, dtype=float)
        self.profile = self.dispatch_input.profile

    def _create_variables(self) -> None:
        self.e_c_in = cp.Variable(self.T)
        self.e_c_out = cp.Variable(self.T)
        self.soc = cp.Variable(self.T)

    def _build_objective(self) -> None:
        net_power = self.e_c_in + self.e_c_out
        energy_term = self.profile.objective_energy_multiplier * self.time_ratio * net_power @ self.price

        demand_term = 0.0
        if self.profile.demand_charge_type == "approx_min_charge":
            demand_term = self.dispatch_input.max_demand_price * cp.min(self.e_c_in)
        elif self.profile.demand_charge_type == "exact_max_net":
            demand_term = -self.dispatch_input.max_demand_price * cp.max(self.demand - net_power)

        smoothing_term = 0.0
        if self.profile.smoothing_enabled:
            smoothing_term = -0.001 * cp.norm(self.e_c_in)

        self.objective = cp.Maximize(energy_term + demand_term + smoothing_term)

    def _build_constraints(self) -> None:
        constraints = []
        for t in range(self.T):
            prev_soc = self.dispatch_input.initial_soc if t == 0 else self.soc[t - 1]
            constraints += [
                self.soc[t] == prev_soc
                - self.e_c_in[t] * self.time_ratio * self.eta_ch
                - self.e_c_out[t] * self.time_ratio / self.eta_dis
            ]

        constraints += [self.e_c_out <= cp.pos(self.demand)]

        if self.profile.demand_peak_guard_constraint:
            demand_load_max = max(self.dispatch_input.demand_load)
            constraints += [self.e_c_in >= cp.pos(self.demand) - demand_load_max]

        if self.profile.transformer_capacity_constraint:
            constraints += [self.demand - self.e_c_in <= self.dispatch_input.transform_capacity]

        constraints += [self.e_c_out <= self.p_dis_max]
        constraints += [self.e_c_out >= 0]
        constraints += [self.e_c_in <= 0]
        constraints += [self.e_c_in >= -self.p_ch_max]
        constraints += [self.soc >= self.soc_min]
        constraints += [self.soc <= self.soc_max]

        self.constraints = constraints

    def _solve_problem(self) -> None:
        self.problem = cp.Problem(self.objective, self.constraints)
        self.objective_value = self.problem.solve(verbose=False, solver=cp.CLARABEL)

    def _extract_solution(self) -> dict[str, list[float] | float]:
        charge_vals = _to_list(-self.e_c_in.value)
        discharge_vals = _to_list(self.e_c_out.value)
        soc_vals = _to_list(self.soc.value)
        return {
            "charge_power": charge_vals,
            "discharge_power": discharge_vals,
            "soc": soc_vals,
            "objective_value": self.objective_value if self.objective_value is not None else 0.0,
        }

    def _build_result(self, values: dict[str, list[float] | float]) -> CvxpBESSDispatchResult:
        charge_vals = values["charge_power"]
        discharge_vals = values["discharge_power"]
        return CvxpBESSDispatchResult(
            charge_power=charge_vals,
            discharge_power=discharge_vals,
            net_power=[round(d_val - c, 6) for c, d_val in zip(charge_vals, discharge_vals)],
            soc=values["soc"],
            objective_value=values["objective_value"],
        )


def get_cvxp_profile(version: str) -> CvxpBESSProfile:
    """根据版本名称获取预定义的 CVXPY 调度算法配置。"""
    if version not in CVXP_PROFILES:
        raise ValueError(f"Unknown version: {version}. Choose from {list(CVXP_PROFILES.keys())}")
    return CVXP_PROFILES[version]


def run_cvxp_bess_dispatch(dispatch_input: CvxpBESSDispatchInput) -> CvxpBESSDispatchResult:
    """CVXPY 凸优化单节点储能调度。

    支持三种需量电费建模模式、L2 平滑惩罚、可选变压器容量约束和需量峰值保护。
    与 run_user_side_bess_dispatch 解决相同场景，但使用 CVXPY 凸优化求解器。
    """
    return CvxpBESSDispatcher(dispatch_input).solve()
