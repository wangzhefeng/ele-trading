"""Intraday rolling storage optimization (§8).

Reuses the rolling-window pattern from mpc_bess but replaces the objective
with the weighted combination of arbitrage, deviation penalty, and demand control.
"""

from __future__ import annotations

import numpy as np
from pulp import LpMinimize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum, value

from ele_trading.trading.contracts import DayAheadPlan, IntradayAdjustment, IntradayPlan, MarketConfig
from ele_trading.utils import check_pulp_status


def solve_intraday_rolling(
    q_real_load: np.ndarray,  # remaining window load (after mid-long deduction)
    p_real_pre: np.ndarray,  # remaining window real price forecast
    q_dayah: np.ndarray,  # cleared day-ahead bid (for deviation penalty)
    p_dayah: np.ndarray,  # cleared day-ahead price
    soc_current: float,
    bess: dict,
    config: MarketConfig,
    prev_p_b: np.ndarray | None = None,  # previous plan for smoothness
) -> IntradayPlan:
    """Solve one intraday rolling window.

    Objective: w_bes*arbitrage + w_pen*deviation_penalty + w_xu*demand_control
    """
    horizon = len(q_real_load)
    dt = 0.25

    m = LpProblem("intraday_rolling", LpMinimize)

    # BESS variables (full power available intraday)
    p_bc = {t: LpVariable(f"p_bc_{t}", lowBound=0, upBound=bess["p_bcmax"]) for t in range(horizon)}
    p_bd = {t: LpVariable(f"p_bd_{t}", lowBound=0, upBound=bess["p_bdmax"]) for t in range(horizon)}
    soc = {t: LpVariable(f"soc_{t}", lowBound=bess["socmin"], upBound=bess["socmax"]) for t in range(horizon)}

    # Deviation penalty auxiliary variables
    q_aux = {t: LpVariable(f"q_aux_{t}", lowBound=0) for t in range(horizon)}

    # SOC dynamics
    for t in range(horizon):
        if t == 0:
            m += soc[t] == soc_current + bess["p_bceff"] * p_bc[t] * dt - (p_bd[t] * dt) / bess["p_bdeff"]
        else:
            m += soc[t] == soc[t - 1] + bess["p_bceff"] * p_bc[t] * dt - (p_bd[t] * dt) / bess["p_bdeff"]

    # Terminal SOC constraint
    terminal_min = config.soc_terminal_min if config.soc_terminal_min is not None else bess["socini"]
    m += soc[horizon - 1] >= terminal_min

    # Deviation penalty linearization (§8.1)
    for t in range(horizon):
        q_real_t = q_real_load[t] + (p_bc[t] - p_bd[t]) * dt
        if p_real_pre[t] > p_dayah[t]:
            m += q_aux[t] >= (q_dayah[t] - config.lam_u * q_real_t) * (p_real_pre[t] - p_dayah[t])
        else:
            m += q_aux[t] >= (config.lam_l * q_real_t - q_dayah[t]) * (p_dayah[t] - p_real_pre[t])

    # Objective components
    obj_bes = lpSum(
        p_real_pre[t] * (p_bd[t] - p_bc[t]) * dt
        - config.deg_cost_per_mwh * (p_bc[t] + p_bd[t]) * dt
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

    reasons = []
    if np.abs(delta_p_b).max() > 0.1:
        reasons.append("price_change")
    if soc_arr[-1] <= terminal_min + 0.01:
        reasons.append("soc_limit")

    schedule = DayAheadPlan(
        p_bc=p_bc_arr,
        p_bd=p_bd_arr,
        p_b=p_b_arr,
        soc=soc_arr,
        q_dayah=q_dayah,  # carry over
        expected_cost=float(value(m.objective)),
        expected_revenue=delta_revenue,
    )

    adjustment = IntradayAdjustment(
        p_b_new=p_b_arr,
        delta_p_b=delta_p_b,
        delta_revenue=delta_revenue,
        reasons=reasons,
    )

    return IntradayPlan(schedule=schedule, adjustment=adjustment)
