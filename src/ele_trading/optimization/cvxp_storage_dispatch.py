from __future__ import annotations

import numpy as np
import cvxpy as cp

from .interfaces import (
    CvxpStorageDispatchInput,
    CvxpStorageDispatchResult,
    CvxpStorageProfile,
)


def run_cvxp_storage_dispatch(
    dispatch_input: CvxpStorageDispatchInput,
) -> CvxpStorageDispatchResult:
    """CVXPY 凸优化单节点储能调度。

    支持三种需量电费建模模式、L2 平滑惩罚、可选变压器容量约束和需量峰值保护。
    与 run_user_side_storage_dispatch 解决相同场景，但使用 CVXPY 凸优化求解器。
    """
    storage = dispatch_input.storage
    T = len(dispatch_input.timestamps)

    # 参数映射: UserSideStorageParams -> 内部数组
    eta_ch = storage.eta_ch
    eta_dis = storage.eta_dis
    p_ch_max = storage.p_ch_max
    p_dis_max = storage.p_dis_max
    soc_max = storage.soc_max
    soc_min = storage.soc_min

    time_ratio = dispatch_input.freq_minutes / 60
    d = np.array(dispatch_input.demand_load)
    p = np.array(dispatch_input.ele_prices)
    profile = dispatch_input.profile

    # 决策变量
    e_c_in = cp.Variable(T)     # 充电功率 (非正)
    e_c_out = cp.Variable(T)    # 放电功率 (非负)
    soc = cp.Variable(T)        # SOC

    # 目标函数
    net_power = e_c_in + e_c_out
    energy_term = profile.objective_energy_multiplier * time_ratio * net_power @ p

    demand_term = 0.0
    if profile.demand_charge_type == "approx_min_charge":
        demand_term = dispatch_input.max_demand_price * cp.min(e_c_in)
    elif profile.demand_charge_type == "exact_max_net":
        demand_term = -dispatch_input.max_demand_price * cp.max(d - net_power)

    smoothing_term = 0.0
    if profile.smoothing_enabled:
        smoothing_term = -0.001 * cp.norm(e_c_in)

    profit = energy_term + demand_term + smoothing_term
    obj = cp.Maximize(profit)

    # 约束条件
    constraints = []

    # SOC 动态约束
    for t in range(T):
        prev_soc = dispatch_input.initial_soc if t == 0 else soc[t - 1]
        constraints += [
            soc[t] == prev_soc
            + e_c_in[t] * time_ratio * eta_ch
            - e_c_out[t] * time_ratio / eta_dis
        ]

    # 放电功率小于等于负荷
    constraints += [e_c_out <= cp.pos(d)]

    # 需量峰值保护约束
    if profile.demand_peak_guard_constraint:
        demand_load_max = max(dispatch_input.demand_load)
        constraints += [e_c_in >= cp.pos(d) - demand_load_max]

    # 变压器容量约束
    if profile.transformer_capacity_constraint:
        constraints += [d - e_c_in <= dispatch_input.transform_capacity]

    # 功率限制
    constraints += [e_c_out <= p_dis_max]
    constraints += [e_c_out >= 0]
    constraints += [e_c_in <= 0]
    constraints += [e_c_in >= -p_ch_max]

    # SOC 容量限制
    constraints += [soc >= soc_min]
    constraints += [soc <= soc_max]

    # 求解
    prob = cp.Problem(obj, constraints)
    result = prob.solve(verbose=False, solver=cp.CLARABEL)

    charge_vals = _to_list(e_c_in.value)
    discharge_vals = _to_list(e_c_out.value)
    soc_vals = _to_list(soc.value)

    return CvxpStorageDispatchResult(
        charge_power=charge_vals,
        discharge_power=discharge_vals,
        net_power=[round(c + d_val, 6) for c, d_val in zip(charge_vals, discharge_vals)],
        soc=soc_vals,
        objective_value=result if result is not None else 0.0,
    )


def _to_list(arr) -> list[float]:
    if arr is None:
        return []
    return [round(float(x), 6) for x in np.asarray(arr).flatten()]


CVXP_PROFILES: dict[str, CvxpStorageProfile] = {
    "without_demand": CvxpStorageProfile(
        objective_energy_multiplier=1.0,
        demand_charge_type="none",
        smoothing_enabled=False,
        transformer_capacity_constraint=False,
        demand_peak_guard_constraint=True,
    ),
    "basic": CvxpStorageProfile(
        objective_energy_multiplier=31.0,
        demand_charge_type="approx_min_charge",
        smoothing_enabled=True,
        transformer_capacity_constraint=False,
        demand_peak_guard_constraint=False,
    ),
    "optim": CvxpStorageProfile(
        objective_energy_multiplier=1.0,
        demand_charge_type="exact_max_net",
        smoothing_enabled=False,
        transformer_capacity_constraint=True,
        demand_peak_guard_constraint=False,
    ),
}


def get_cvxp_profile(version: str) -> CvxpStorageProfile:
    """根据版本名称获取预定义的 CVXPY 调度算法配置。"""
    if version not in CVXP_PROFILES:
        raise ValueError(f"Unknown version: {version}. Choose from {list(CVXP_PROFILES.keys())}")
    return CVXP_PROFILES[version]
