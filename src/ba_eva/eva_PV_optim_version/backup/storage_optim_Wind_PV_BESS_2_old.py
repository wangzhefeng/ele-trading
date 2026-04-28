# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim11.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042018
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from typing import Dict

import numpy as np
import pandas as pd
from ba_eva.eva_PV_optim_version.storage_optim_common import (
    njit, NUMBA_OK,
    PlanConfigFast,
)


@njit
def _dispatch_annual_fast_numba(load_kw, 
                                wind_kw, 
                                pv_kw, 
                                other_kw,
                                dt_hours, 
                                batt_kwh,
                                eta_roundtrip, 
                                c_rate,
                                soc_init_frac, 
                                soc_min_frac, 
                                soc_max_frac):
    gen_e = 0.0
    used_e = 0.0
    load_e = 0.0
    direct_e = 0.0
    bess_dis = 0.0

    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5

    E = batt_kwh
    Pmax = c_rate * E

    soc_min = soc_min_frac * E
    soc_max = soc_max_frac * E
    soc = soc_init_frac * E
    if soc < soc_min:
        soc = soc_min
    if soc > soc_max:
        soc = soc_max

    n = load_kw.shape[0]

    for i in range(n):
        # ---- load ----
        L = load_kw[i]
        if L < 0.0:
            L = 0.0

        # ---- generation ----
        G = wind_kw[i] + pv_kw[i] + other_kw[i]
        if G < 0.0:
            G = 0.0

        load_e += L * dt_hours
        gen_e += G * dt_hours

        # ---- direct use ----
        direct = L if L < G else G
        used_e += direct * dt_hours
        direct_e += direct * dt_hours

        surplus = G - direct
        deficit = L - direct

        # ---- charge ----
        if surplus > 1e-9 and soc < soc_max:
            p_ch = surplus
            if p_ch > Pmax:
                p_ch = Pmax
            max_ch = (soc_max - soc) / dt_hours
            if p_ch > max_ch:
                p_ch = max_ch
            soc += p_ch * dt_hours * eta_c

        # ---- discharge ----
        if deficit > 1e-9 and soc > soc_min:
            p_dis = deficit
            if p_dis > Pmax:
                p_dis = Pmax
            max_dis = (soc - soc_min) * eta_d / dt_hours
            if p_dis > max_dis:
                p_dis = max_dis
            soc -= p_dis * dt_hours / eta_d
            used_e += p_dis * dt_hours
            bess_dis += p_dis * dt_hours

    return gen_e, used_e, load_e, direct_e, bess_dis

# TODO wind_fixed_pv_bess_fast
def plan_wind_fixed_pv_bess_fast(
    df_2025: pd.DataFrame,
    pv_unit_kw: pd.Series,        # kW / kWp
    wind_kw: pd.Series,           # kW（50MW 风电）
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: PlanConfigFast = PlanConfigFast(),
) -> Dict[str, object]:

    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)

    dt_hours = (df[time_col].diff().dropna().mode().iloc[0]).total_seconds() / 3600.0

    load_kw = df[load_col].to_numpy(dtype="float64")
    wind_kw = wind_kw.reindex(df[time_col]).fillna(0.0).to_numpy(dtype="float64")
    unit_kw = pv_unit_kw.reindex(df[time_col]).interpolate("time").fillna(0.0).to_numpy(dtype="float64")

    # 预留：其他新能源
    other_kw = np.zeros_like(load_kw)

    load_kwh_total = load_kw.sum() * dt_hours
    wind_kwh_total = wind_kw.sum() * dt_hours

    peak_load = load_kw.max()
    pv_max_kwp = cfg.pv_max_kwp or max(cfg.pv_step_coarse_kwp, 3.0 * peak_load)

    best = None

    pv_candidates = np.arange(cfg.pv_step_coarse_kwp, pv_max_kwp + 1e-9, cfg.pv_step_coarse_kwp)

    for pv_kwp in pv_candidates:
        pv_kw = unit_kw * pv_kwp
        pv_kwh = pv_kw.sum() * dt_hours

        # ---- 快速能量剪枝 ----
        if wind_kwh_total + pv_kwh < cfg.load_cover_ratio_min * load_kwh_total:
            continue

        # ---- 无电池 ----
        gen_e, used_e, load_e, _, _ = _dispatch_annual_fast_numba(
            load_kw, wind_kw, pv_kw, other_kw,
            dt_hours, 0.0,
            cfg.eta_roundtrip, cfg.c_rate,
            cfg.soc_init_frac, cfg.soc_min_frac, cfg.soc_max_frac
        )

        if gen_e <= 0:
            continue

        self_use = used_e / gen_e
        cover = used_e / load_e

        if self_use < cfg.self_use_ratio_min or cover < cfg.load_cover_ratio_min:
            continue

        # ---- CAPEX ----
        pv_capex = pv_kwp * cfg.pv_capex_yuan_per_kwp

        if best is None or pv_capex < best["total_capex_yuan"]:
            best = {
                "pv_kwp": float(pv_kwp),
                "bess_kwh": 0.0,
                "total_capex_yuan": pv_capex,
                "self_use_ratio": self_use,
                "load_cover_ratio": cover,
                "wind_gen_kwh": wind_kwh_total,
                "pv_gen_kwh": pv_kwh,
            }

    if best is None:
        raise ValueError("未找到满足新能源自用率/覆盖率约束的方案")

    # ---- 月度发电量 ----
    tmp = df.copy()
    tmp["m"] = tmp[time_col].dt.to_period("M")
    pv_monthly = (unit_kw * best["pv_kwp"] * dt_hours)
    wind_monthly = (wind_kw * dt_hours)

    pv_monthly_kwh = tmp.groupby("m").apply(lambda x: pv_monthly[x.index].sum())
    wind_monthly_kwh = tmp.groupby("m").apply(lambda x: wind_monthly[x.index].sum())

    best["pv_monthly_kwh"] = pv_monthly_kwh
    best["wind_monthly_kwh"] = wind_monthly_kwh

    return best




# 测试代码 main 函数
def main():
    cfg = PlanConfigFast(
        load_cover_ratio_min=0.35,
        batt_bisect_iter=24
    )
if __name__ == "__main__":
    main()
