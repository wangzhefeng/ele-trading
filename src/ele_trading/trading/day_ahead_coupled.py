"""Day-ahead coupled storage-trading optimization (§7).

Implements modes A/B/C for joint storage scheduling and day-ahead bidding.
Mode B (effective marginal price) is the default per v1 design document.
"""

from __future__ import annotations

import numpy as np
from pulp import LpMaximize, LpMinimize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum, value

from ele_trading.trading.contracts import DayAheadPlan, MarketConfig
from ele_trading.utils import check_pulp_status


def solve_day_ahead_coupled(
    q_load_pre: np.ndarray,  # (96,) MWh/刻, open load after mid-long deduction
    p_dayah_pre: np.ndarray,  # (96,) 元/MWh
    p_real_pre: np.ndarray,  # (96,) 元/MWh
    bess: dict,  # BES object §4.3
    config: MarketConfig,
    mode: str = "B",
) -> DayAheadPlan:
    """Solve day-ahead coupled optimization.

    Modes:
        A: real-price arbitrage
        B: effective marginal price (default)
        C: joint bid quantity optimization
    """
    if mode == "A":
        return _solve_mode_a(q_load_pre, p_real_pre, bess, config)
    elif mode == "B":
        return _solve_mode_b(q_load_pre, p_dayah_pre, p_real_pre, bess, config)
    elif mode == "C":
        return _solve_mode_c(q_load_pre, p_dayah_pre, p_real_pre, bess, config)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _build_bess_vars(
    model: LpProblem,
    horizon: int,
    bess: dict,
    config: MarketConfig,
    prefix: str = "",
) -> tuple[dict, dict, dict]:
    """Build BESS charge/discharge/SOC variables with constraints."""
    margin = config.dayahead_power_margin
    p_bc = {t: LpVariable(f"{prefix}p_bc_{t}", lowBound=0, upBound=margin * bess["p_bcmax"]) for t in range(horizon)}
    p_bd = {t: LpVariable(f"{prefix}p_bd_{t}", lowBound=0, upBound=margin * bess["p_bdmax"]) for t in range(horizon)}
    soc = {t: LpVariable(f"{prefix}soc_{t}", lowBound=bess["socmin"], upBound=bess["socmax"]) for t in range(horizon)}

    dt = 0.25  # 15 min
    for t in range(horizon):
        if t == 0:
            model += soc[t] == bess["socini"] + bess["p_bceff"] * p_bc[t] * dt - (p_bd[t] * dt) / bess["p_bdeff"]
        else:
            model += soc[t] == soc[t - 1] + bess["p_bceff"] * p_bc[t] * dt - (p_bd[t] * dt) / bess["p_bdeff"]

    # Terminal SOC constraint
    terminal_min = config.soc_terminal_min if config.soc_terminal_min is not None else bess["socini"]
    model += soc[horizon - 1] >= terminal_min

    # Throughput limit
    if config.throughput_max_ratio > 0:
        model += lpSum((p_bc[t] + p_bd[t]) * dt for t in range(horizon)) <= config.throughput_max_ratio * 2 * bess["cap"]

    return p_bc, p_bd, soc


def _solve_mode_a(
    q_load_pre: np.ndarray,
    p_real_pre: np.ndarray,
    bess: dict,
    config: MarketConfig,
) -> DayAheadPlan:
    """Mode A: real-price arbitrage."""
    horizon = len(q_load_pre)
    dt = 0.25
    m = LpProblem("dayahead_mode_a", LpMaximize)
    p_bc, p_bd, soc = _build_bess_vars(m, horizon, bess, config)

    # Arbitrage objective
    m += lpSum(
        p_real_pre[t] * (p_bd[t] - p_bc[t]) * dt
        - config.deg_cost_per_mwh * (p_bc[t] + p_bd[t]) * dt
        for t in range(horizon)
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "dayahead mode A")

    p_bc_arr = np.array([value(p_bc[t]) for t in range(horizon)])
    p_bd_arr = np.array([value(p_bd[t]) for t in range(horizon)])
    p_b_arr = p_bd_arr - p_bc_arr
    soc_arr = np.array([bess["socini"]] + [value(soc[t]) for t in range(horizon)])

    # Bid = base load (no deviation from forecast in mode A)
    q_base = q_load_pre - p_b_arr * dt
    q_dayah = q_base.copy()

    expected_revenue = float(value(m.objective))
    expected_cost = float(np.sum(q_dayah * p_real_pre))  # rough estimate

    return DayAheadPlan(
        p_bc=p_bc_arr,
        p_bd=p_bd_arr,
        p_b=p_b_arr,
        soc=soc_arr,
        q_dayah=q_dayah,
        expected_cost=expected_cost,
        expected_revenue=expected_revenue,
    )


def _solve_mode_b(
    q_load_pre: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    bess: dict,
    config: MarketConfig,
) -> DayAheadPlan:
    """Mode B: effective marginal price (default)."""
    horizon = len(q_load_pre)
    dt = 0.25
    m = LpProblem("dayahead_mode_b", LpMinimize)

    # Effective price (§7.2 Mode B)
    pi_eff = np.where(
        p_real_pre > p_dayah_pre,
        config.lam_l * p_dayah_pre + (1 - config.lam_l) * p_real_pre,
        config.lam_u * p_dayah_pre + (1 - config.lam_u) * p_real_pre,
    )

    p_bc, p_bd, soc = _build_bess_vars(m, horizon, bess, config)

    # Minimize effective cost
    m += lpSum(
        (p_bc[t] - p_bd[t]) * dt * pi_eff[t]
        + config.deg_cost_per_mwh * (p_bc[t] + p_bd[t]) * dt
        for t in range(horizon)
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "dayahead mode B")

    p_bc_arr = np.array([value(p_bc[t]) for t in range(horizon)])
    p_bd_arr = np.array([value(p_bd[t]) for t in range(horizon)])
    p_b_arr = p_bd_arr - p_bc_arr
    soc_arr = np.array([bess["socini"]] + [value(soc[t]) for t in range(horizon)])

    # Bid generation with deviation band rules (§7.3)
    q_base = q_load_pre - p_b_arr * dt
    q_dayah = _apply_bid_rules(q_base, p_dayah_pre, p_real_pre, config)

    expected_cost = float(value(m.objective))
    expected_revenue = float(-expected_cost)  # cost minimization

    return DayAheadPlan(
        p_bc=p_bc_arr,
        p_bd=p_bd_arr,
        p_b=p_b_arr,
        soc=soc_arr,
        q_dayah=q_dayah,
        expected_cost=expected_cost,
        expected_revenue=expected_revenue,
    )


def _solve_mode_c(
    q_load_pre: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    bess: dict,
    config: MarketConfig,
) -> DayAheadPlan:
    """Mode C: joint bid quantity optimization."""
    horizon = len(q_load_pre)
    dt = 0.25
    m = LpProblem("dayahead_mode_c", LpMinimize)

    p_bc, p_bd, soc = _build_bess_vars(m, horizon, bess, config)

    # Bid quantity variables
    q_dayah_opt = {t: LpVariable(f"q_dayah_opt_{t}", lowBound=0) for t in range(horizon)}
    q_aux = {t: LpVariable(f"q_aux_{t}", lowBound=0) for t in range(horizon)}

    # Deviation penalty linearization (§7.2 Mode C)
    for t in range(horizon):
        q_real_t = q_load_pre[t] + (p_bc[t] - p_bd[t]) * dt
        if p_real_pre[t] > p_dayah_pre[t]:
            m += q_aux[t] >= (q_dayah_opt[t] - config.lam_u * q_real_t) * (p_real_pre[t] - p_dayah_pre[t])
        else:
            m += q_aux[t] >= (config.lam_l * q_real_t - q_dayah_opt[t]) * (p_dayah_pre[t] - p_real_pre[t])

    # Objective: energy cost + penalty
    obj_ecost = lpSum(
        q_dayah_opt[t] * p_dayah_pre[t]
        + (q_load_pre[t] + (p_bc[t] - p_bd[t]) * dt - q_dayah_opt[t]) * p_real_pre[t]
        for t in range(horizon)
    )
    obj_pen = lpSum(q_aux[t] for t in range(horizon))
    m += obj_ecost + config.w_pen * obj_pen

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "dayahead mode C")

    p_bc_arr = np.array([value(p_bc[t]) for t in range(horizon)])
    p_bd_arr = np.array([value(p_bd[t]) for t in range(horizon)])
    p_b_arr = p_bd_arr - p_bc_arr
    soc_arr = np.array([bess["socini"]] + [value(soc[t]) for t in range(horizon)])
    q_dayah = np.array([value(q_dayah_opt[t]) for t in range(horizon)])

    expected_cost = float(value(m.objective))
    expected_revenue = float(-expected_cost)

    return DayAheadPlan(
        p_bc=p_bc_arr,
        p_bd=p_bd_arr,
        p_b=p_b_arr,
        soc=soc_arr,
        q_dayah=q_dayah,
        expected_cost=expected_cost,
        expected_revenue=expected_revenue,
    )


def _apply_bid_rules(
    q_base: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    config: MarketConfig,
) -> np.ndarray:
    """Apply deviation-band bid rules (§7.3)."""
    q_dayah = q_base.copy()
    gap = config.gap
    k = config.bias_k

    mask_da_expensive = p_dayah_pre > p_real_pre + gap
    mask_da_cheap = p_dayah_pre < p_real_pre - gap

    q_dayah[mask_da_expensive] = config.lam_l**k * q_base[mask_da_expensive]
    q_dayah[mask_da_cheap] = config.lam_u**k * q_base[mask_da_cheap]

    # Price limit clipping (§7.4)
    q_dayah = np.clip(q_dayah, config.price_floor, config.price_cap)

    return q_dayah
