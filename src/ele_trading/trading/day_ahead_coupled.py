"""Day-ahead coupled storage-trading optimization (v1.3 §6).

Implements modes A/B/C for joint storage scheduling and day-ahead bidding.
Mode B (effective marginal price) is the default per v1.3 design document.
"""

from __future__ import annotations

import numpy as np
from pulp import LpBinary, LpMaximize, LpMinimize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum, value

from ele_trading.trading.contracts import DayAheadPlan, MarketConfig
from ele_trading.utils import check_pulp_status
from ele_trading.utils.log_util import logger

DT = 0.25  # 15 min 决策粒度（v1.3 §2.1）


def solve_day_ahead_coupled(
    q_load_pre: np.ndarray,  # (96,) MWh/刻, open load after mid-long deduction
    p_dayah_pre: np.ndarray,  # (96,) 元/MWh
    p_real_pre: np.ndarray,  # (96,) 元/MWh
    bess: dict,  # BES object §2.3
    config: MarketConfig,
    mode: str = "B",
    t_curt: list[int] | None = None,  # 限电/新能源大发时段集合（no_discharge_on_curtail=True 时生效）
    q_long: np.ndarray | None = None,  # (96,) 中长期持仓（风控带一致告警告用，可选）
) -> DayAheadPlan:
    """Solve day-ahead coupled optimization.

    Modes:
        A: real-price arbitrage
        B: effective marginal price (default)
        C: joint bid quantity optimization
    """
    _check_no_nan(q_load_pre, "q_load_pre")
    _check_no_nan(p_dayah_pre, "p_dayah_pre")
    _check_no_nan(p_real_pre, "p_real_pre")
    if mode == "A":
        return _solve_mode_a(q_load_pre, p_dayah_pre, p_real_pre, bess, config, t_curt, q_long)
    elif mode == "B":
        return _solve_mode_b(q_load_pre, p_dayah_pre, p_real_pre, bess, config, t_curt, q_long)
    elif mode == "C":
        return _solve_mode_c(q_load_pre, p_dayah_pre, p_real_pre, bess, config, t_curt, q_long)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _check_no_nan(arr: np.ndarray, name: str) -> None:
    """预测输入含 NaN 时默认报错，不容忍静默前向填充（v1.3 §11.4.2）。"""
    if np.isnan(arr).any():
        raise ValueError(f"{name} contains NaN; optimization modules reject NaN forecasts (v1.3 §11.4.2)")


def _build_bess_vars(
    model: LpProblem,
    horizon: int,
    bess: dict,
    config: MarketConfig,
    q_load: np.ndarray,
    t_curt: list[int] | None = None,
    prefix: str = "",
) -> tuple[dict, dict, dict]:
    """Build BESS charge/discharge/SOC variables with constraints (v1.3 §2.3)."""
    margin = config.dayahead_power_margin
    p_bc = {t: LpVariable(f"{prefix}p_bc_{t}", lowBound=0, upBound=margin * bess["p_bcmax"]) for t in range(horizon)}
    p_bd = {t: LpVariable(f"{prefix}p_bd_{t}", lowBound=0, upBound=margin * bess["p_bdmax"]) for t in range(horizon)}
    soc = {t: LpVariable(f"{prefix}soc_{t}", lowBound=bess["socmin"], upBound=bess["socmax"]) for t in range(horizon)}

    # 限电/新能源大发时段禁放（v1.3 §2.3 可选约束）
    if config.no_discharge_on_curtail and t_curt:
        for t in t_curt:
            if 0 <= t < horizon:
                model += p_bd[t] == 0

    # 充放互斥（MILP，默认关闭保持 LP；负价或惩罚主导时开启，v1.3 §2.3）
    if config.exclusive_charge_discharge:
        cap_p = max(bess["p_bcmax"], bess["p_bdmax"])
        z = {t: LpVariable(f"{prefix}z_chg_{t}", cat=LpBinary) for t in range(horizon)}
        for t in range(horizon):
            model += p_bc[t] <= cap_p * z[t]
            model += p_bd[t] <= cap_p * (1 - z[t])

    for t in range(horizon):
        if t == 0:
            model += soc[t] == bess["socini"] + bess["p_bceff"] * p_bc[t] * DT - (p_bd[t] * DT) / bess["p_bdeff"]
        else:
            model += soc[t] == soc[t - 1] + bess["p_bceff"] * p_bc[t] * DT - (p_bd[t] * DT) / bess["p_bdeff"]
        # 净负荷非负，默认不可倒送（v1.3 §2.3）
        model += q_load[t] + (p_bc[t] - p_bd[t]) * DT >= 0

    # Terminal SOC constraint
    terminal_min = config.soc_terminal_min if config.soc_terminal_min is not None else bess["socini"]
    model += soc[horizon - 1] >= terminal_min

    # Throughput limit
    if config.throughput_max_ratio > 0:
        model += lpSum((p_bc[t] + p_bd[t]) * DT for t in range(horizon)) <= config.throughput_max_ratio * 2 * bess["cap"]

    return p_bc, p_bd, soc


def _collect_constraint_flags(
    p_bc_arr: np.ndarray,
    p_bd_arr: np.ndarray,
    soc_arr: np.ndarray,
    q_load: np.ndarray,
    bess: dict,
    config: MarketConfig,
) -> dict[str, list[int]]:
    """求解后审计约束激活时段（v1.3 §6.5 约束提示）。"""
    tol = 1e-3
    margin = config.dayahead_power_margin
    flags: dict[str, list[int]] = {}
    flags["soc_at_max"] = [t for t in range(len(soc_arr)) if soc_arr[t] >= bess["socmax"] - tol]
    flags["soc_at_min"] = [t for t in range(len(soc_arr)) if soc_arr[t] <= bess["socmin"] + tol]
    flags["p_c_at_limit"] = [t for t in range(len(p_bc_arr)) if p_bc_arr[t] >= margin * bess["p_bcmax"] - tol]
    flags["p_d_at_limit"] = [t for t in range(len(p_bd_arr)) if p_bd_arr[t] >= margin * bess["p_bdmax"] - tol]
    net_load = q_load + (p_bc_arr - p_bd_arr) * DT
    flags["no_reverse_active"] = [t for t in range(len(q_load)) if net_load[t] <= tol]
    return {k: v for k, v in flags.items() if v}


def _solve_mode_a(
    q_load_pre: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    bess: dict,
    config: MarketConfig,
    t_curt: list[int] | None = None,
    q_long: np.ndarray | None = None,
) -> DayAheadPlan:
    """Mode A: real-price arbitrage."""
    horizon = len(q_load_pre)
    m = LpProblem("dayahead_mode_a", LpMaximize)
    p_bc, p_bd, soc = _build_bess_vars(m, horizon, bess, config, q_load_pre, t_curt)

    # Arbitrage objective
    m += lpSum(
        p_real_pre[t] * (p_bd[t] - p_bc[t]) * DT
        - config.deg_cost_per_mwh * (p_bc[t] + p_bd[t]) * DT
        for t in range(horizon)
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "dayahead mode A")

    p_bc_arr = np.array([value(p_bc[t]) for t in range(horizon)])
    p_bd_arr = np.array([value(p_bd[t]) for t in range(horizon)])
    p_b_arr = p_bd_arr - p_bc_arr
    soc_arr = np.array([bess["socini"]] + [value(soc[t]) for t in range(horizon)])

    # Bid = base load (no deviation from forecast in mode A)
    q_base = q_load_pre - p_b_arr * DT
    q_dayah = _apply_bid_rules(q_base, p_dayah_pre, p_real_pre, config)
    q_dayah = _apply_risk_clipping(q_dayah, q_base, q_long, config)

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
        constraint_flags=_collect_constraint_flags(p_bc_arr, p_bd_arr, soc_arr, q_load_pre, bess, config),
        bid_prices=_make_bid_prices(q_dayah, p_dayah_pre, config),
    )


def _solve_mode_b(
    q_load_pre: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    bess: dict,
    config: MarketConfig,
    t_curt: list[int] | None = None,
    q_long: np.ndarray | None = None,
) -> DayAheadPlan:
    """Mode B: effective marginal price (default)."""
    horizon = len(q_load_pre)
    m = LpProblem("dayahead_mode_b", LpMinimize)

    # Effective price (v1.3 §6.2 Mode B)
    pi_eff = np.where(
        p_real_pre > p_dayah_pre,
        config.lam_l * p_dayah_pre + (1 - config.lam_l) * p_real_pre,
        config.lam_u * p_dayah_pre + (1 - config.lam_u) * p_real_pre,
    )

    p_bc, p_bd, soc = _build_bess_vars(m, horizon, bess, config, q_load_pre, t_curt)

    # Minimize effective cost
    m += lpSum(
        (p_bc[t] - p_bd[t]) * DT * pi_eff[t]
        + config.deg_cost_per_mwh * (p_bc[t] + p_bd[t]) * DT
        for t in range(horizon)
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "dayahead mode B")

    p_bc_arr = np.array([value(p_bc[t]) for t in range(horizon)])
    p_bd_arr = np.array([value(p_bd[t]) for t in range(horizon)])
    p_b_arr = p_bd_arr - p_bc_arr
    soc_arr = np.array([bess["socini"]] + [value(soc[t]) for t in range(horizon)])

    # Bid generation with deviation band rules (v1.3 §6.4) + 风控裁剪
    q_base = q_load_pre - p_b_arr * DT
    q_dayah = _apply_bid_rules(q_base, p_dayah_pre, p_real_pre, config)
    q_dayah = _apply_risk_clipping(q_dayah, q_base, q_long, config)

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
        constraint_flags=_collect_constraint_flags(p_bc_arr, p_bd_arr, soc_arr, q_load_pre, bess, config),
        bid_prices=_make_bid_prices(q_dayah, p_dayah_pre, config),
    )


def _solve_mode_c(
    q_load_pre: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    bess: dict,
    config: MarketConfig,
    t_curt: list[int] | None = None,
    q_long: np.ndarray | None = None,
) -> DayAheadPlan:
    """Mode C: joint bid quantity optimization."""
    horizon = len(q_load_pre)
    m = LpProblem("dayahead_mode_c", LpMinimize)

    p_bc, p_bd, soc = _build_bess_vars(m, horizon, bess, config, q_load_pre, t_curt)

    # Bid quantity variables
    q_dayah_opt = {t: LpVariable(f"q_dayah_opt_{t}", lowBound=0) for t in range(horizon)}
    q_aux = {t: LpVariable(f"q_aux_{t}", lowBound=0) for t in range(horizon)}

    # Deviation penalty linearization (v1.3 §6.2 Mode C)
    for t in range(horizon):
        q_real_t = q_load_pre[t] + (p_bc[t] - p_bd[t]) * DT
        if p_real_pre[t] > p_dayah_pre[t]:
            m += q_aux[t] >= (q_dayah_opt[t] - config.lam_u * q_real_t) * (p_real_pre[t] - p_dayah_pre[t])
        else:
            m += q_aux[t] >= (config.lam_l * q_real_t - q_dayah_opt[t]) * (p_dayah_pre[t] - p_real_pre[t])

    # Objective: energy cost + penalty
    obj_ecost = lpSum(
        q_dayah_opt[t] * p_dayah_pre[t]
        + (q_load_pre[t] + (p_bc[t] - p_bd[t]) * DT - q_dayah_opt[t]) * p_real_pre[t]
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
    q_dayah_opt_arr = np.array([value(q_dayah_opt[t]) for t in range(horizon)])

    # 模式 C 融合（v1.3 §6.4）：优化量有效时覆盖规则量，否则回退
    q_base = q_load_pre - p_b_arr * DT
    q_dayah = _fuse_mode_c_bid(q_dayah_opt_arr, q_base, p_dayah_pre, p_real_pre, config)
    q_dayah = _apply_risk_clipping(q_dayah, q_base, q_long, config)

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
        constraint_flags=_collect_constraint_flags(p_bc_arr, p_bd_arr, soc_arr, q_load_pre, bess, config),
        bid_prices=_make_bid_prices(q_dayah, p_dayah_pre, config),
    )


def _fuse_mode_c_bid(
    q_dayah_opt: np.ndarray,
    q_base: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    config: MarketConfig,
) -> np.ndarray:
    """模式 C 申报量融合（v1.3 §6.4）。

    优化量为有效解（有限、非负）时直接用优化量；否则回退到 §6.4 规则量。
    """
    valid = np.isfinite(q_dayah_opt).all() and (q_dayah_opt >= -1e-9).all()
    if valid:
        return np.maximum(q_dayah_opt, 0.0)
    logger.warning("mode C 优化申报量无效（含非有限值或负值），回退到规则申报量")
    return _apply_bid_rules(q_base, p_dayah_pre, p_real_pre, config)


def _make_bid_prices(
    q_dayah: np.ndarray,
    p_dayah_pre: np.ndarray,
    config: MarketConfig,
) -> np.ndarray | None:
    """分段申报价（v1.3 §6.5）。

    蒙西默认报量不报价 → 返回 None；price_reporting=True 时按预测出清价
    生成申报价并统一裁剪到 [price_floor, price_cap]。
    """
    if not config.dayahead_price_reporting:
        return None
    return np.clip(p_dayah_pre.copy(), config.price_floor, config.price_cap)


def _apply_bid_rules(
    q_base: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real_pre: np.ndarray,
    config: MarketConfig,
) -> np.ndarray:
    """Apply deviation-band bid rules (v1.3 §6.4).

    日前偏贵 → 少报压 lam_l 下界；日前偏便宜 → 多报压 lam_u 上界。
    申报量为电量（MWh/刻），只按物理非负裁剪；[price_floor, price_cap]
    为申报价限值，由分段申报价生成处裁剪（v1.3 §6.5）。
    """
    q_dayah = q_base.copy()
    gap = config.gap
    k = config.bias_k

    mask_da_expensive = p_dayah_pre > p_real_pre + gap
    mask_da_cheap = p_dayah_pre < p_real_pre - gap

    q_dayah[mask_da_expensive] = config.lam_l**k * q_base[mask_da_expensive]
    q_dayah[mask_da_cheap] = config.lam_u**k * q_base[mask_da_cheap]

    return np.maximum(q_dayah, 0.0)


def _apply_risk_clipping(
    q_dayah: np.ndarray,
    q_base: np.ndarray,
    q_long: np.ndarray | None,
    config: MarketConfig,
) -> np.ndarray:
    """日前申报量风控裁剪（v1.3 §6.4，参数化三规则）。

    规则顺序：单点变化率限制 → 日总量上下限 → 中长期带一致告警（不强制）。
    触发时记录日志（§11.4.3）。
    """
    out = q_dayah.copy()
    horizon = len(out)

    # 规则 1：单点变化率限制 —— 相邻刻申报量变化 ≤ max_step_ratio * Q_base
    ratio = config.risk_max_step_ratio
    if ratio > 0 and horizon > 1:
        n_clipped = 0
        for t in range(1, horizon):
            step_limit = ratio * max(q_base[t], 1e-6)
            delta = out[t] - out[t - 1]
            if abs(delta) > step_limit:
                out[t] = out[t - 1] + np.sign(delta) * step_limit
                n_clipped += 1
        if n_clipped:
            logger.info(f"风控裁剪[max_step_ratio]: {n_clipped}/{horizon} 刻被限幅 (ratio={ratio})")

    # 规则 2：日总量上下限 —— 申报总量 ∈ ΣQ_base × [1-band, 1+band]
    band = config.risk_daily_qty_band
    total_base = float(np.sum(q_base))
    total = float(np.sum(out))
    lo, hi = total_base * (1 - band), total_base * (1 + band)
    if total > hi and total > 0:
        out *= hi / total
        logger.info(f"风控裁剪[daily_qty_band]: 日总量 {total:.1f} → {hi:.1f} MWh (+{band:.0%} 上限)")
    elif total < lo and total > 0:
        out *= lo / total
        logger.info(f"风控裁剪[daily_qty_band]: 日总量 {total:.1f} → {lo:.1f} MWh (-{band:.0%} 下限)")

    # 规则 3：中长期带一致 —— 申报量与 Q_long 合计越出中长期考核带时告警（不强制）
    if config.risk_long_band_check and q_long is not None:
        covered = out + q_long
        breach = (covered < config.lam_l_long * q_base) | (covered > config.lam_u_long * q_base)
        if breach.any():
            t_list = [int(t) for t in np.nonzero(breach)[0]]
            logger.warning(
                f"风控告警[long_band_check]: {len(t_list)} 刻 申报+Q_long 越出中长期带 "
                f"[{config.lam_l_long}, {config.lam_u_long}]: t={t_list[:10]}"
            )

    return np.maximum(out, 0.0)
