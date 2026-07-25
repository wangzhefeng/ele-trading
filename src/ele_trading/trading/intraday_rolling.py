"""Intraday rolling storage optimization (v1.3 §7).

Reuses the rolling-window pattern from mpc_bess but replaces the objective
with the weighted combination of arbitrage, deviation penalty, and demand control.
"""

from __future__ import annotations

import numpy as np
from pulp import LpBinary, LpMinimize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum, value

from ele_trading.trading.contracts import DayAheadPlan, IntradayAdjustment, IntradayPlan, MarketConfig
from ele_trading.utils import check_pulp_status

DT = 0.25  # 15 min 决策粒度（v1.3 §2.1）


def solve_intraday_rolling(
    q_real_load: np.ndarray,  # remaining window load (after mid-long deduction)
    p_real_pre: np.ndarray,  # remaining window real price forecast
    q_dayah: np.ndarray,  # cleared day-ahead bid (for deviation penalty)
    p_dayah: np.ndarray,  # cleared day-ahead price
    soc_current: float,
    bess: dict,
    config: MarketConfig,
    prev_p_b: np.ndarray | None = None,  # previous plan for smoothness
    t_curt: list[int] | None = None,  # 限电时段（no_discharge_on_curtail=True 时生效）
    dr_lock_p_b: np.ndarray | None = None,  # DR 履约时段锁定功率曲线（DR_FULFILL）
) -> IntradayPlan:
    """Solve one intraday rolling window.

    Objective: w_bes*arbitrage + w_pen*deviation_penalty (+ smoothness)
    """
    _check_no_nan(q_real_load, "q_real_load")
    _check_no_nan(p_real_pre, "p_real_pre")
    _check_no_nan(q_dayah, "q_dayah")
    _check_no_nan(p_dayah, "p_dayah")

    horizon = len(q_real_load)

    m = LpProblem("intraday_rolling", LpMinimize)

    # BESS variables (full power available intraday)
    p_bc = {t: LpVariable(f"p_bc_{t}", lowBound=0, upBound=bess["p_bcmax"]) for t in range(horizon)}
    p_bd = {t: LpVariable(f"p_bd_{t}", lowBound=0, upBound=bess["p_bdmax"]) for t in range(horizon)}
    soc = {t: LpVariable(f"soc_{t}", lowBound=bess["socmin"], upBound=bess["socmax"]) for t in range(horizon)}

    # Deviation penalty auxiliary variables
    q_aux = {t: LpVariable(f"q_aux_{t}", lowBound=0) for t in range(horizon)}

    # 限电时段禁放（v1.3 §2.3）
    if config.no_discharge_on_curtail and t_curt:
        for t in t_curt:
            if 0 <= t < horizon:
                m += p_bd[t] == 0

    # 充放互斥（MILP，默认关闭；v1.3 §2.3）
    if config.exclusive_charge_discharge:
        cap_p = max(bess["p_bcmax"], bess["p_bdmax"])
        z = {t: LpVariable(f"z_chg_{t}", cat=LpBinary) for t in range(horizon)}
        for t in range(horizon):
            m += p_bc[t] <= cap_p * z[t]
            m += p_bd[t] <= cap_p * (1 - z[t])

    # SOC dynamics + 不可倒送（v1.3 §2.3）
    for t in range(horizon):
        if t == 0:
            m += soc[t] == soc_current + bess["p_bceff"] * p_bc[t] * DT - (p_bd[t] * DT) / bess["p_bdeff"]
        else:
            m += soc[t] == soc[t - 1] + bess["p_bceff"] * p_bc[t] * DT - (p_bd[t] * DT) / bess["p_bdeff"]
        m += q_real_load[t] + (p_bc[t] - p_bd[t]) * DT >= 0

    # Terminal SOC constraint
    terminal_min = config.soc_terminal_min if config.soc_terminal_min is not None else bess["socini"]
    m += soc[horizon - 1] >= terminal_min

    # DR 履约锁定：响应时段 p_b 固定为响应曲线（DR_FULFILL，v1.3 §9）
    if dr_lock_p_b is not None:
        for t in range(min(len(dr_lock_p_b), horizon)):
            if not np.isnan(dr_lock_p_b[t]):
                m += p_bd[t] - p_bc[t] == dr_lock_p_b[t]

    # Deviation penalty linearization (v1.3 §7.1)
    for t in range(horizon):
        q_real_t = q_real_load[t] + (p_bc[t] - p_bd[t]) * DT
        if p_real_pre[t] > p_dayah[t]:
            m += q_aux[t] >= (q_dayah[t] - config.lam_u * q_real_t) * (p_real_pre[t] - p_dayah[t])
        else:
            m += q_aux[t] >= (config.lam_l * q_real_t - q_dayah[t]) * (p_dayah[t] - p_real_pre[t])

    # Objective components
    obj_bes = lpSum(
        p_real_pre[t] * (p_bd[t] - p_bc[t]) * DT
        - config.deg_cost_per_mwh * (p_bc[t] + p_bd[t]) * DT
        for t in range(horizon)
    )
    obj_pen = lpSum(q_aux[t] for t in range(horizon))

    # Smoothness penalty (if previous plan provided) - linearized with auxiliary vars
    obj_smooth = 0
    if prev_p_b is not None and len(prev_p_b) == horizon:
        smooth_weight = 0.1
        delta_pos = {t: LpVariable(f"delta_pos_{t}", lowBound=0) for t in range(horizon)}
        delta_neg = {t: LpVariable(f"delta_neg_{t}", lowBound=0) for t in range(horizon)}
        for t in range(horizon):
            p_b_t = p_bd[t] - p_bc[t]
            m += delta_pos[t] >= p_b_t - prev_p_b[t]
            m += delta_neg[t] >= prev_p_b[t] - p_b_t
        obj_smooth = lpSum(smooth_weight * (delta_pos[t] + delta_neg[t]) for t in range(horizon))

    m += -config.w_bes * obj_bes + config.w_pen * obj_pen + obj_smooth

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "intraday rolling")

    p_bc_arr = np.array([value(p_bc[t]) for t in range(horizon)])
    p_bd_arr = np.array([value(p_bd[t]) for t in range(horizon)])
    p_b_arr = p_bd_arr - p_bc_arr
    soc_arr = np.array([soc_current] + [value(soc[t]) for t in range(horizon)])

    # Compute adjustment metrics
    delta_p_b = p_b_arr - (prev_p_b if prev_p_b is not None else np.zeros(horizon))
    delta_revenue = float(-value(m.objective))  # minimize → negative is revenue

    # 调整原因（v1.3 §7.2 输出增量）
    reasons = []
    if np.abs(delta_p_b).max() > 0.1:
        reasons.append("price_change")
    if prev_p_b is not None and len(prev_p_b) == horizon:
        # 平滑项显著激活说明负荷/价差结构较上版变化大
        smooth_val = float(np.abs(delta_p_b).sum())
        if smooth_val > 0.1 * horizon * bess["p_bdmax"] * 0.1:
            reasons.append("load_change")
    if soc_arr[-1] <= terminal_min + 0.01:
        reasons.append("soc_limit")
    if dr_lock_p_b is not None and np.any(~np.isnan(dr_lock_p_b)):
        reasons.append("dr")

    schedule = DayAheadPlan(
        p_bc=p_bc_arr,
        p_bd=p_bd_arr,
        p_b=p_b_arr,
        soc=soc_arr,
        q_dayah=q_dayah,  # carry over
        expected_cost=float(value(m.objective)),
        expected_revenue=delta_revenue,
        constraint_flags=_collect_constraint_flags(p_bc_arr, p_bd_arr, soc_arr, q_real_load, bess),
    )

    adjustment = IntradayAdjustment(
        p_b_new=p_b_arr,
        delta_p_b=delta_p_b,
        delta_revenue=delta_revenue,
        reasons=reasons,
    )

    return IntradayPlan(schedule=schedule, adjustment=adjustment)


def _check_no_nan(arr: np.ndarray, name: str) -> None:
    """预测输入含 NaN 时默认报错，不容忍静默前向填充（v1.3 §11.4.2）。"""
    if np.isnan(arr).any():
        raise ValueError(f"{name} contains NaN; optimization modules reject NaN forecasts (v1.3 §11.4.2)")


def _collect_constraint_flags(
    p_bc_arr: np.ndarray,
    p_bd_arr: np.ndarray,
    soc_arr: np.ndarray,
    q_load: np.ndarray,
    bess: dict,
) -> dict[str, list[int]]:
    """求解后审计约束激活时段（v1.3 §6.5 约束提示）。"""
    tol = 1e-3
    flags: dict[str, list[int]] = {}
    flags["soc_at_max"] = [t for t in range(len(soc_arr)) if soc_arr[t] >= bess["socmax"] - tol]
    flags["soc_at_min"] = [t for t in range(len(soc_arr)) if soc_arr[t] <= bess["socmin"] + tol]
    flags["p_c_at_limit"] = [t for t in range(len(p_bc_arr)) if p_bc_arr[t] >= bess["p_bcmax"] - tol]
    flags["p_d_at_limit"] = [t for t in range(len(p_bd_arr)) if p_bd_arr[t] >= bess["p_bdmax"] - tol]
    net_load = q_load + (p_bc_arr - p_bd_arr) * DT
    flags["no_reverse_active"] = [t for t in range(len(q_load)) if net_load[t] <= tol]
    return {k: v for k, v in flags.items() if v}
