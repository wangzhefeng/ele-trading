"""公共调度算法模块

提供年度贪心调度引擎（Python + Numba 双版本），供各容量规划脚本共用。
"""
from __future__ import annotations

from typing import Dict

import numpy as np

# Numba 兼容层
try:
    from numba import njit
    _NUMBA_OK = True
except Exception:
    _NUMBA_OK = False

    def njit(*args, **kwargs):
        def deco(f):
            return f
        return deco


@njit
def _dispatch_annual_numba(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    batt_kwh: float,
    eta_roundtrip: float,
    c_rate: float,
    soc_init_frac: float,
    soc_min_frac: float,
    soc_max_frac: float,
    switch_gap_steps: int,
) -> tuple[float, float, float, float, float, float]:
    """
    贪心调度（Numba JIT），支持充放切换间隔。
    # TODO 补充注释
    """
    gen_e = 0.0  # TODO 补充注释
    used_e = 0.0  # TODO 补充注释
    load_e = 0.0  # TODO 补充注释
    direct_e = 0.0  # TODO 补充注释
    bess_dis = 0.0  # TODO 补充注释
    curtail_e = 0.0  # TODO 补充注释
    
    # TODO 补充注释
    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5
    
    # TODO 补充注释
    E = batt_kwh

    # TODO 补充注释
    Pmax = c_rate * E

    # TODO 补充注释
    soc_min = soc_min_frac * E
    soc_max = soc_max_frac * E
    soc = soc_init_frac * E
    if soc < soc_min:
        soc = soc_min
    if soc > soc_max:
        soc = soc_max

    # TODO 补充注释
    last_action = 0  # +1=charge, -1=discharge, 0=idle
    last_action_t = -10**9

    # TODO 补充注释
    n = load_kw.shape[0]
    for i in range(n):
        # 负荷 (kW)
        L = load_kw[i]
        if L < 0.0:
            L = 0.0
        # 新能源发电 (kW)
        G = wind_kw[i] + pv_kw[i] + other_kw[i]
        if G < 0.0:
            G = 0.0

        # 负荷电能量
        load_e += L * dt_hours
        # 新能源发电电能量
        gen_e += G * dt_hours

        # 直供部分 (kW)
        direct = L if L < G else G
        used_e += direct * dt_hours
        direct_e += direct * dt_hours

        # TODO 完善注释 多余电 (kW)
        surplus = G - direct
        # TODO 完善注释 缺额 (kW)
        deficit = L - direct

        # 判断是否可以充放电（考虑切换间隔）
        can_charge = not (last_action == -1 and (i - last_action_t) < switch_gap_steps)
        can_discharge = not (last_action == 1 and (i - last_action_t) < switch_gap_steps)

        # 充电：surplus 多余且未到 SOC 上限
        ch_actual = 0.0
        if surplus > 1e-9 and E > 0.0 and soc < soc_max and can_charge:
            p_ch = surplus
            if p_ch > Pmax:
                p_ch = Pmax
            max_ch = (soc_max - soc) / dt_hours
            if p_ch > max_ch:
                p_ch = max_ch
            if p_ch > 1e-9:
                soc += p_ch * dt_hours * eta_c
                ch_actual = p_ch
                last_action = 1
                last_action_t = i
        # 放电：deficit 缺额且未到 SOC 下限
        if deficit > 1e-9 and E > 0.0 and soc > soc_min and can_discharge:
            p_dis = deficit
            if p_dis > Pmax:
                p_dis = Pmax
            max_dis = (soc - soc_min) * eta_d / dt_hours
            if p_dis > max_dis:
                p_dis = max_dis
            if p_dis > 1e-9:
                soc -= p_dis * dt_hours / eta_d
                used_e += p_dis * dt_hours
                bess_dis += p_dis * dt_hours
                last_action = -1
                last_action_t = i
        # 弃电 = surplus - 实际充电量 (surplus 实际未能存入电池的部分)
        curtail_e += max(surplus - ch_actual, 0.0) * dt_hours

    return gen_e, used_e, load_e, direct_e, bess_dis, curtail_e


def dispatch_annual(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    batt_kwh: float,
    dt_hours: float,
    cfg: Dict,
    switch_gap_steps: int = 0,
) -> dict[str, float]:
    if cfg.use_numba and _NUMBA_OK:
        gen_e, used_e, load_e, direct_e, bess_dis, curtail_e = _dispatch_annual_numba(
            load_kw = load_kw,
            wind_kw = wind_kw,
            pv_kw = pv_kw,
            other_kw = other_kw,
            batt_kwh = float(batt_kwh),
            dt_hours = dt_hours,
            eta_roundtrip = float(cfg.eta_roundtrip),
            c_rate = float(cfg.c_rate),
            soc_init_frac = float(cfg.soc_init_frac),
            soc_min_frac = float(cfg.soc_min_frac),
            soc_max_frac = float(cfg.soc_max_frac),
            switch_gap_steps = switch_gap_steps,
        )
    else:
        # TODO 补充注释
        gen_kw = wind_kw + pv_kw + other_kw
        # TODO 补充注释
        direct_kw = np.minimum(load_kw, np.maximum(gen_kw, 0.0))
        # TODO 补充注释
        gen_e = float(np.maximum(gen_kw, 0.0).sum() * dt_hours)
        # TODO 补充注释
        load_e = float(np.maximum(load_kw, 0.0).sum() * dt_hours)
        # TODO 补充注释
        used_e = float(direct_kw.sum() * dt_hours)
        direct_e = used_e
        # TODO 补充注释
        bess_dis = 0.0
        # 弃电量（简化：surplus 部分减去充电）
        surplus_e = float(np.maximum(gen_kw - load_kw, 0.0).sum() * dt_hours)
        curtail_e = max(surplus_e - (gen_e - used_e - bess_dis), 0.0)

    return {
        "ren_gen_kwh": float(gen_e),
        "ren_used_kwh": float(used_e),
        "load_kwh": float(load_e),
        "direct_used_kwh": float(direct_e),
        "bess_discharge_kwh": float(bess_dis),
        "curtail_kwh": float(curtail_e),
    }
