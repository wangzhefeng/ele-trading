# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim_Wind_PV_BESS_combine.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-28
# * Version     : 1.0.0
# * Description : 整合 Wind+PV+BESS 容量规划算法
#                 - 主框架: storage_optim_Wind_PV_BESS_3 (PV 搜索 + Numba 加速)
#                 - 集成: storage_optim_Wind_PV_BESS_1 (能量门槛检查 + 充放切换间隔)
# ***************************************************

# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from typing import Optional, Dict, Any, Tuple, Union

import numpy as np
import pandas as pd
from wind_pv_es_calc.storage_optim_common import (
    njit, NUMBA_OK,
    BESSConfig, PlanConfigFast,
    infer_dt_hours, align_to_time, monthly_kwh,
    as_time_series, ensure_time_sorted,
)


# ============================================================
# 数据对齐（来自 BESS_1）
# ============================================================
def align_curves(
    df_load: pd.DataFrame,
    df_pv: pd.DataFrame,
    df_wind: pd.DataFrame,
    load_col: str,
    pv_col: str,
    wind_col: str,
    time_col: str = "Time",
) -> pd.DataFrame:
    """
    输出统一表：Time, load_kw, pv_kw, wind_kw, gen_kw
        - wind_col 输入为 MW 时会自动转 kW
    """
    df_load = ensure_time_sorted(df_load, time_col)
    df_pv = ensure_time_sorted(df_pv, time_col)
    df_wind = ensure_time_sorted(df_wind, time_col)

    a = df_load[[time_col, load_col]].rename(columns={load_col: "load_kw"})
    b = df_pv[[time_col, pv_col]].rename(columns={pv_col: "pv_kw"})
    c = df_wind[[time_col, wind_col]].rename(columns={wind_col: "wind_mw"})

    df = a.merge(b, on=time_col, how="outer").merge(c, on=time_col, how="outer")
    df = df.sort_values(time_col).reset_index(drop=True)

    df["load_kw"] = pd.to_numeric(df["load_kw"], errors="coerce").fillna(0.0)
    df["pv_kw"] = pd.to_numeric(df["pv_kw"], errors="coerce").fillna(0.0)
    df["wind_kw"] = pd.to_numeric(df["wind_mw"], errors="coerce").fillna(0.0) * 1000.0
    df["gen_kw"] = df["pv_kw"] + df["wind_kw"]
    df = df.drop(columns=["wind_mw"])

    return df


# ============================================================
# 能量门槛检查（来自 BESS_1）
# ============================================================
def energy_gate_check(
    df_mix: pd.DataFrame,
    freq: str = "1h",
    target_ratio: float = 0.30,
) -> Dict[str, Any]:
    """
    先判断：发电量(风+光) / 用电量 是否 >= target_ratio
    仅能量层面，不考虑弃电与储能。
    """
    dt_h = pd.to_timedelta(freq).total_seconds() / 3600.0
    load_kwh = float(df_mix["load_kw"].sum() * dt_h)
    gen_kwh = float(df_mix["gen_kw"].sum() * dt_h)
    ratio = 0.0 if load_kwh <= 0 else gen_kwh / load_kwh

    return {
        "load_total_kwh": load_kwh,
        "gen_total_kwh": gen_kwh,
        "gen_ratio": ratio,
        "pass_gate": (ratio >= target_ratio),
    }


# ============================================================
# 年度调度 - Numba 加速版（来自 BESS_3，集成充放切换间隔）
# ============================================================
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
) -> Tuple[float, float, float, float, float]:
    """贪心调度（Numba JIT），支持充放切换间隔。"""
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

    last_action = 0  # +1=charge, -1=discharge, 0=idle
    last_action_t = -10**9

    n = load_kw.shape[0]
    for i in range(n):
        L = load_kw[i]
        if L < 0.0:
            L = 0.0

        G = wind_kw[i] + pv_kw[i] + other_kw[i]
        if G < 0.0:
            G = 0.0

        load_e += L * dt_hours
        gen_e += G * dt_hours

        direct = L if L < G else G
        used_e += direct * dt_hours
        direct_e += direct * dt_hours

        surplus = G - direct
        deficit = L - direct

        # 判断是否可以充放电（考虑切换间隔）
        can_charge = not (last_action == -1 and (i - last_action_t) < switch_gap_steps)
        can_discharge = not (last_action == 1 and (i - last_action_t) < switch_gap_steps)

        # charge
        if surplus > 1e-9 and E > 0.0 and soc < soc_max and can_charge:
            p_ch = surplus
            if p_ch > Pmax:
                p_ch = Pmax
            max_ch = (soc_max - soc) / dt_hours
            if p_ch > max_ch:
                p_ch = max_ch
            if p_ch > 1e-9:
                soc += p_ch * dt_hours * eta_c
                last_action = 1
                last_action_t = i

        # discharge
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

    return gen_e, used_e, load_e, direct_e, bess_dis


def _dispatch_annual(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    batt_kwh: float,
    cfg: PlanConfigFast,
    switch_gap_steps: int = 0,
) -> Dict[str, float]:
    if cfg.use_numba and NUMBA_OK:
        gen_e, used_e, load_e, direct_e, bess_dis = _dispatch_annual_numba(
            load_kw, wind_kw, pv_kw, other_kw,
            dt_hours, float(batt_kwh),
            float(cfg.eta_roundtrip), float(cfg.c_rate),
            float(cfg.soc_init_frac), float(cfg.soc_min_frac), float(cfg.soc_max_frac),
            switch_gap_steps,
        )
    else:
        # Python fallback
        gen_kw = wind_kw + pv_kw + other_kw
        direct_kw = np.minimum(load_kw, np.maximum(gen_kw, 0.0))
        gen_e = float(np.maximum(gen_kw, 0.0).sum() * dt_hours)
        load_e = float(np.maximum(load_kw, 0.0).sum() * dt_hours)
        used_e = float(direct_kw.sum() * dt_hours)
        direct_e = used_e
        bess_dis = 0.0

    return {
        "ren_gen_kwh": float(gen_e),
        "ren_used_kwh": float(used_e),
        "load_kwh": float(load_e),
        "direct_used_kwh": float(direct_e),
        "bess_discharge_kwh": float(bess_dis),
    }


# ============================================================
# 电池二分：给定 PV，找最小 BESS（来自 BESS_3）
# ============================================================
def _find_min_bess_kwh(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    cfg: PlanConfigFast,
    switch_gap_steps: int = 0,
) -> Optional[Tuple[float, Dict[str, float]]]:
    """
    返回 (bess_kwh, stats)；若找不到可行解返回 None
    """
    def feasible(batt_kwh: float) -> Tuple[bool, Dict[str, float]]:
        st = _dispatch_annual(load_kw, wind_kw, pv_kw, other_kw, dt_hours, batt_kwh, cfg, switch_gap_steps)
        gen = st["ren_gen_kwh"]
        used = st["ren_used_kwh"]
        load = st["load_kwh"]
        if gen <= 1e-9:
            return False, st
        self_use = used / gen
        cover = used / load if load > 1e-9 else 0.0
        ok = (self_use >= cfg.self_use_ratio_min) and (cover >= cfg.load_cover_ratio_min)
        st["self_use_ratio"] = self_use
        st["load_cover_ratio"] = cover
        return ok, st

    # 先试 0
    ok0, st0 = feasible(0.0)
    if ok0:
        return 0.0, st0

    # 扩上界
    hi = float(cfg.batt_hi_init_kwh)
    for _ in range(40):
        if hi > cfg.batt_hi_max_kwh:
            return None
        ok, st = feasible(hi)
        if ok:
            break
        hi *= 2.0
    else:
        return None

    lo = 0.0
    best_kwh = hi
    best_st = st
    # 二分
    for _ in range(cfg.batt_bisect_iter):
        mid = 0.5 * (lo + hi)
        ok, st_mid = feasible(mid)
        if ok:
            best_kwh, best_st = mid, st_mid
            hi = mid
        else:
            lo = mid
        if (hi - lo) <= cfg.batt_tol_kwh:
            break

    return float(best_kwh), best_st


# ============================================================
# 主规划函数（整合版）
# ============================================================
def plan_wind_pv_bess(
    df_load: pd.DataFrame,
    pv_unit_kw: Union[pd.Series, pd.DataFrame],
    wind_input: Union[pd.Series, pd.DataFrame],
    *,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: PlanConfigFast = PlanConfigFast(),
    wind_unit: str = "MW",
    pv_unit: str = "kW",
    other_input: Optional[Union[pd.Series, pd.DataFrame]] = None,
    other_unit: str = "kW",
    enable_gate_check: bool = True,
    gate_target_ratio: float = 0.30,
    switch_gap_hours: float = 0.0,
) -> Dict[str, Any]:
    """
    Wind+PV+BESS 容量规划主入口。

    Args:
        df_load: 负荷数据 DataFrame
        pv_unit_kw: 光伏单位出力 (kW/kWp)
        wind_input: 风电数据 (MW 或 kW)
        load_col: 负荷列名
        time_col: 时间列名
        cfg: 规划配置
        wind_unit: 风电单位 ("MW" 或 "kW")
        pv_unit: 光伏单位 ("kW" 或 "MW")
        other_input: 其他新能源输入（可选）
        other_unit: 其他新能源单位
        enable_gate_check: 是否启用能量门槛检查
        gate_target_ratio: 能量门槛目标比例
        switch_gap_hours: 充放切换间隔（小时）

    Returns:
        dict: 规划结果
    """
    # ---- 负荷 ----
    df = df_load[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)

    # ---- 风电 ----
    wind_scale = 1000.0 if wind_unit.lower() == "mw" else 1.0
    wind_s = as_time_series(
        wind_input,
        time_col=time_col,
        value_cols=("WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower"),
        scale=wind_scale,
    )
    wind_kw_arr = align_to_time(df[time_col], wind_s)

    # ---- PV ----
    pv_scale = 1000.0 if pv_unit.lower() == "mw" else 1.0
    pv_unit_s = as_time_series(
        pv_unit_kw,
        time_col=time_col,
        value_cols=("pv_unit_kw", "pv_kw", "u", "value"),
        scale=pv_scale,
    )
    pv_unit_arr = align_to_time(df[time_col], pv_unit_s)

    # ---- 其他新能源 ----
    if other_input is None:
        other_kw_arr = np.zeros_like(load_kw_arr, dtype=np.float64)
    else:
        other_scale = 1.0 if other_unit.lower() == "kw" else 1000.0
        other_s = as_time_series(
            other_input,
            time_col=time_col,
            value_cols=("other_kw", "OtherPower_kW", "OtherPower"),
            scale=other_scale,
        )
        other_kw_arr = align_to_time(df[time_col], other_s)

    # ---- 基础量 ----
    dt_hours = infer_dt_hours(df[time_col])
    load_kwh_total = float(load_kw_arr.sum() * dt_hours)
    wind_kwh_total = float(wind_kw_arr.sum() * dt_hours)
    peak_load = float(load_kw_arr.max()) if len(load_kw_arr) else 0.0
    pv_max_kwp = cfg.pv_max_kwp or max(cfg.pv_step_coarse_kwp, 3.0 * peak_load)
    wind_monthly_kwh = monthly_kwh(df[time_col], wind_kw_arr, dt_hours)

    # ---- 充放切换间隔（步数）----
    switch_gap_steps = int(round(switch_gap_hours / dt_hours)) if switch_gap_hours > 0 else 0

    # ---- 能量门槛检查 ----
    gate_result = None
    if enable_gate_check:
        # 计算最大 PV 时的总发电量
        pv_max_kw_arr = pv_unit_arr * pv_max_kwp
        pv_max_kwh = float(pv_max_kw_arr.sum() * dt_hours)
        total_gen_kwh = wind_kwh_total + pv_max_kwh
        gen_ratio = total_gen_kwh / load_kwh_total if load_kwh_total > 0 else 0.0

        gate_result = {
            "load_total_kwh": load_kwh_total,
            "gen_total_kwh": total_gen_kwh,
            "gen_ratio": gen_ratio,
            "pass_gate": (gen_ratio >= gate_target_ratio),
        }

        if not gate_result["pass_gate"]:
            return {
                "status": "gate_failed",
                "gate": gate_result,
                "message": (
                    f"能量门槛未通过：风+光年发电量占比={gen_ratio:.3f}，"
                    f"低于目标{gate_target_ratio:.2f}。"
                ),
            }

    # ==========================
    # PV 搜索：粗扫 + 可选细扫
    # ==========================
    best = None
    best_pv_kwp_coarse = None
    pv_candidates = np.arange(cfg.pv_min_kwp, pv_max_kwp + 1e-9, cfg.pv_step_coarse_kwp)

    for pv_kwp in pv_candidates:
        pv_kw_arr = pv_unit_arr * float(pv_kwp)
        pv_kwh = float(pv_kw_arr.sum() * dt_hours)

        # 快速能量剪枝
        if (wind_kwh_total + pv_kwh) < cfg.load_cover_ratio_min * load_kwh_total:
            continue

        # 可行性判断
        if cfg.enable_bess:
            found = _find_min_bess_kwh(
                load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
                dt_hours, cfg, switch_gap_steps,
            )
            if found is None:
                continue
            bess_kwh, st = found
        else:
            st = _dispatch_annual(
                load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
                dt_hours, 0.0, cfg, switch_gap_steps,
            )
            if st["ren_gen_kwh"] <= 1e-9:
                continue
            self_use = st["ren_used_kwh"] / st["ren_gen_kwh"]
            cover = st["ren_used_kwh"] / st["load_kwh"] if st["load_kwh"] > 1e-9 else 0.0
            if (self_use < cfg.self_use_ratio_min) or (cover < cfg.load_cover_ratio_min):
                continue
            st["self_use_ratio"] = self_use
            st["load_cover_ratio"] = cover
            bess_kwh = 0.0

        # 计算总成本
        pv_capex = float(pv_kwp) * cfg.pv_capex_yuan_per_kwp
        bess_capex = float(bess_kwh) * cfg.bess_capex_yuan_per_kwh
        total_capex = pv_capex + bess_capex

        if (best is None) or (total_capex < best["total_capex_yuan"]):
            best = {
                "pv_kwp": float(pv_kwp),
                "bess_kwh": float(bess_kwh),
                "pv_capex_yuan": pv_capex,
                "bess_capex_yuan": bess_capex,
                "total_capex_yuan": total_capex,
                "self_use_ratio": float(st["self_use_ratio"]),
                "load_cover_ratio": float(st["load_cover_ratio"]),
                "ren_gen_kwh": float(st["ren_gen_kwh"]),
                "ren_used_kwh": float(st["ren_used_kwh"]),
                "direct_used_kwh": float(st["direct_used_kwh"]),
                "bess_discharge_kwh": float(st["bess_discharge_kwh"]),
                "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python",
            }
            best_pv_kwp_coarse = float(pv_kwp)

    if best is None:
        return {
            "status": "no_solution",
            "message": "未找到满足新能源自用率/覆盖率约束的方案：请扩大 pv_max_kwp 或放宽比例阈值。",
        }

    # ---- 可选：细扫 ----
    if cfg.pv_step_fine_kwp > 0 and best_pv_kwp_coarse is not None:
        lo = max(cfg.pv_min_kwp, best_pv_kwp_coarse - cfg.pv_refine_window_kwp)
        hi = min(pv_max_kwp, best_pv_kwp_coarse + cfg.pv_refine_window_kwp)
        fine_candidates = np.arange(lo, hi + 1e-9, cfg.pv_step_fine_kwp)

        for pv_kwp in fine_candidates:
            pv_kw_arr = pv_unit_arr * float(pv_kwp)
            pv_kwh = float(pv_kw_arr.sum() * dt_hours)

            if (wind_kwh_total + pv_kwh) < cfg.load_cover_ratio_min * load_kwh_total:
                continue

            if cfg.enable_bess:
                found = _find_min_bess_kwh(
                    load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
                    dt_hours, cfg, switch_gap_steps,
                )
                if found is None:
                    continue
                bess_kwh, st = found
            else:
                st = _dispatch_annual(
                    load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
                    dt_hours, 0.0, cfg, switch_gap_steps,
                )
                if st["ren_gen_kwh"] <= 1e-9:
                    continue
                self_use = st["ren_used_kwh"] / st["ren_gen_kwh"]
                cover = st["ren_used_kwh"] / st["load_kwh"] if st["load_kwh"] > 1e-9 else 0.0
                if (self_use < cfg.self_use_ratio_min) or (cover < cfg.load_cover_ratio_min):
                    continue
                st["self_use_ratio"] = self_use
                st["load_cover_ratio"] = cover
                bess_kwh = 0.0

            pv_capex = float(pv_kwp) * cfg.pv_capex_yuan_per_kwp
            bess_capex = float(bess_kwh) * cfg.bess_capex_yuan_per_kwh
            total_capex = pv_capex + bess_capex

            if total_capex < best["total_capex_yuan"]:
                best.update({
                    "pv_kwp": float(pv_kwp),
                    "bess_kwh": float(bess_kwh),
                    "pv_capex_yuan": pv_capex,
                    "bess_capex_yuan": bess_capex,
                    "total_capex_yuan": total_capex,
                    "self_use_ratio": float(st["self_use_ratio"]),
                    "load_cover_ratio": float(st["load_cover_ratio"]),
                    "ren_gen_kwh": float(st["ren_gen_kwh"]),
                    "ren_used_kwh": float(st["ren_used_kwh"]),
                    "direct_used_kwh": float(st["direct_used_kwh"]),
                    "bess_discharge_kwh": float(st["bess_discharge_kwh"]),
                })

    # ==========================
    # 输出年/月 PV、风电发电量
    # ==========================
    pv_gen_kw_arr = pv_unit_arr * float(best["pv_kwp"])
    pv_gen_kwh_total = float(pv_gen_kw_arr.sum() * dt_hours)
    pv_monthly_kwh = monthly_kwh(df[time_col], pv_gen_kw_arr, dt_hours)

    return {
        "status": "ok",
        "pv_kwp": best["pv_kwp"],
        "bess_kwh": best["bess_kwh"],
        "self_use_ratio": best["self_use_ratio"],
        "load_cover_ratio": best["load_cover_ratio"],
        "pv_gen_kwh_annual": pv_gen_kwh_total,
        "pv_gen_kwh_monthly": pv_monthly_kwh,
        "wind_gen_kwh_annual": wind_kwh_total,
        "wind_gen_kwh_monthly": wind_monthly_kwh,
        "pv_capex_yuan": best["pv_capex_yuan"],
        "bess_capex_yuan": best["bess_capex_yuan"],
        "total_capex_yuan": best["total_capex_yuan"],
        "engine": best["engine"],
        "gate": gate_result,
        "switch_gap_hours": switch_gap_hours,
        "debug": {
            "ren_gen_kwh": best["ren_gen_kwh"],
            "ren_used_kwh": best["ren_used_kwh"],
            "direct_used_kwh": best["direct_used_kwh"],
            "bess_discharge_kwh": best["bess_discharge_kwh"],
            "dt_hours": dt_hours,
            "pv_max_kwp_used": pv_max_kwp,
        },
    }


# ============================================================
# 简化版主评估函数（来自 BESS_1 风格，PV 固定）
# ============================================================
def evaluate_wind_pv_bess(
    df_load: pd.DataFrame,
    pv_kw: pd.DataFrame,
    df_wind: pd.DataFrame,
    load_col: str = "P_kw",
    pv_col: str = "pv_kw",
    wind_col: str = "WindPower_MW",
    time_col: str = "Time",
    target_ratio: float = 0.30,
    cfg: Optional[BESSConfig] = None,
    switch_gap_hours: float = 1.0,
) -> Dict[str, Any]:
    """
    评估固定 PV + Wind + BESS 的方案（PV 不参与搜索）。

    Args:
        df_load: 负荷数据
        pv_kw: 光伏出力数据 (kW)
        df_wind: 风电数据 (MW)
        load_col: 负荷列名
        pv_col: 光伏列名
        wind_col: 风电列名
        time_col: 时间列名
        target_ratio: 能量门槛目标比例
        cfg: BESS 配置
        switch_gap_hours: 充放切换间隔

    Returns:
        dict: 评估结果
    """
    if cfg is None:
        cfg = BESSConfig()

    # 数据对齐
    df_mix = align_curves(
        df_load=df_load,
        df_pv=pv_kw,
        df_wind=df_wind,
        load_col=load_col,
        pv_col=pv_col,
        wind_col=wind_col,
        time_col=time_col,
    )

    # 能量门槛检查
    gate = energy_gate_check(df_mix, freq=cfg.freq, target_ratio=target_ratio)

    out = {
        "df_mix": df_mix,
        "gate": gate,
        "status": "gate_failed" if not gate["pass_gate"] else "gate_passed",
    }

    if not gate["pass_gate"]:
        out["message"] = (
            f"能量门槛未通过：风+光年发电量占比={gate['gen_ratio']:.3f}，"
            f"低于目标{target_ratio:.2f}。"
        )
        return out

    # BESS 评估
    dt_h = pd.to_timedelta(cfg.freq).total_seconds() / 3600.0
    gap_steps = int(round(switch_gap_hours / dt_h)) if switch_gap_hours > 0 else 0

    bess_res = _estimate_min_bess_capacity(df_mix, cfg, target_ratio, gap_steps)
    out["bess_result"] = bess_res

    return out


def _estimate_min_bess_capacity(
    df_mix: pd.DataFrame,
    cfg: BESSConfig,
    target_cover_ratio: float = 0.30,
    gap_steps: int = 0,
) -> Dict[str, Any]:
    """二分搜索最小 BESS 容量。"""
    dt_h = pd.to_timedelta(cfg.freq).total_seconds() / 3600.0

    def simulate(bess_kwh: float) -> Dict[str, Any]:
        cap_kwh = max(bess_kwh, 0.0)
        pmax_kw = 0.0 if cap_kwh <= 0 else cap_kwh / cfg.hours_to_full

        e = cap_kwh * cfg.soc_init
        e_min = cap_kwh * cfg.soc_min
        e_max = cap_kwh * cfg.soc_max

        last_action = 0
        last_action_t = -10**9

        direct_used_kwh = 0.0
        discharge_used_kwh = 0.0
        curtailed_kwh = 0.0
        load_total_kwh = 0.0

        for i in range(len(df_mix)):
            load = float(df_mix.iloc[i]["load_kw"])
            gen = float(df_mix.iloc[i]["gen_kw"])

            load_total_kwh += load * dt_h
            direct = min(load, gen)
            direct_used_kwh += direct * dt_h

            surplus = max(gen - load, 0.0)
            deficit = max(load - gen, 0.0)

            can_charge = not (last_action == -1 and (i - last_action_t) < gap_steps)
            can_discharge = not (last_action == 1 and (i - last_action_t) < gap_steps)

            # charge
            p_ch = 0.0
            if surplus > 0 and cap_kwh > 0 and can_charge:
                p_ch_cap = (e_max - e) / max(dt_h * cfg.eta_charge, 1e-9)
                p_ch = min(surplus, pmax_kw, p_ch_cap)
                if p_ch > 1e-9:
                    e += p_ch * dt_h * cfg.eta_charge
                    last_action, last_action_t = 1, i

            # discharge
            p_dis = 0.0
            if deficit > 0 and cap_kwh > 0 and can_discharge:
                p_dis_cap = (e - e_min) * cfg.eta_discharge / max(dt_h, 1e-9)
                p_dis = min(deficit, pmax_kw, p_dis_cap)
                if p_dis > 1e-9:
                    e -= (p_dis * dt_h) / cfg.eta_discharge
                    discharge_used_kwh += p_dis * dt_h
                    last_action, last_action_t = -1, i

            curtailed = max(surplus - p_ch, 0.0)
            curtailed_kwh += curtailed * dt_h

        renewable_used_kwh = direct_used_kwh + discharge_used_kwh
        cover_ratio = 0.0 if load_total_kwh <= 0 else renewable_used_kwh / load_total_kwh

        return {
            "bess_kwh": cap_kwh,
            "pmax_kw": pmax_kw,
            "load_total_kwh": load_total_kwh,
            "direct_used_kwh": direct_used_kwh,
            "discharge_used_kwh": discharge_used_kwh,
            "renewable_used_kwh": renewable_used_kwh,
            "cover_ratio": cover_ratio,
            "curtailed_kwh": curtailed_kwh,
        }

    # 无需储能
    baseline = simulate(0.0)
    if baseline["cover_ratio"] >= target_cover_ratio:
        return {"status": "no_bess_needed", "baseline": baseline, "best": baseline}

    # 扩上界
    surplus_kw = np.maximum(df_mix["gen_kw"].values - df_mix["load_kw"].values, 0.0)
    cap_high_kwh = max(1_000.0, float(np.max(surplus_kw)) * 6.0)

    lo, hi = 0.0, float(cap_high_kwh)
    res_hi = simulate(hi)
    expand = 0
    while res_hi["cover_ratio"] < target_cover_ratio and expand < 12:
        hi *= 2.0
        res_hi = simulate(hi)
        expand += 1

    if res_hi["cover_ratio"] < target_cover_ratio:
        return {"status": "not_reachable", "baseline": baseline, "try_hi": res_hi}

    best = res_hi
    for _ in range(28):
        mid = 0.5 * (lo + hi)
        res_mid = simulate(mid)
        if res_mid["cover_ratio"] >= target_cover_ratio:
            best = res_mid
            hi = mid
        else:
            lo = mid
        if (hi - lo) / max(hi, 1.0) < 0.01:
            break

    return {"status": "ok", "baseline": baseline, "best": best}


# ============================================================
# 测试代码
# ============================================================
def main():
    from wind_pv_es_calc.eva_PV_optim_version.data_processing import data_processor

    df_load, df_pv, df_wind = data_processor(
        load_transfer_coef=685436401 / 704234268,
        farm_capacity_mw=110.0,
        mean_wind_speed_140m=5.5,
        eq_full_load_hours=1920.7,
        lat=28.42,
        lon=117.88,
        capacity_kwp=1,
        data_combine=False,
    )

    # ====================
    # 模式 1: PV 搜索 + BESS 搜索（原 BESS_3 风格）
    # ====================
    print("=" * 50)
    print("模式 1: PV 搜索 + BESS 搜索")
    print("=" * 50)

    cfg = PlanConfigFast(
        load_cover_ratio_min=0.35,
        batt_bisect_iter=24,
    )
    res = plan_wind_pv_bess(
        df_load=df_load,
        pv_unit_kw=df_pv,
        wind_input=df_wind,
        load_col="P_kw",
        time_col="Time",
        cfg=cfg,
        wind_unit="MW",
        enable_gate_check=True,
        gate_target_ratio=0.30,
        switch_gap_hours=0.0,
    )
    print("状态:", res["status"])
    if res["status"] == "ok":
        print("光伏容量：PV(kWp):", res["pv_kwp"])
        print("储能容量：BESS(kWh):", res["bess_kwh"])
        print("新能源自用率:", res["self_use_ratio"])
        print("负荷覆盖率:", res["load_cover_ratio"])
        print("投资(元):", res["total_capex_yuan"])

    # ====================
    # 模式 2: 固定 PV + BESS 评估（原 BESS_1 风格）
    # ====================
    print("\n" + "=" * 50)
    print("模式 2: 固定 PV + BESS 评估")
    print("=" * 50)

    cfg_bess = BESSConfig(soc_min=0.0)
    res2 = evaluate_wind_pv_bess(
        df_load=df_load,
        pv_kw=df_pv,
        df_wind=df_wind,
        load_col="P_kw",
        pv_col="pv_kw",
        wind_col="WindPower_MW",
        time_col="Time",
        target_ratio=0.30,
        cfg=cfg_bess,
        switch_gap_hours=1.0,
    )
    print("状态:", res2["status"])
    if res2["status"] == "gate_passed":
        br = res2["bess_result"]
        print("储能评估状态:", br["status"])
        if br["status"] == "ok":
            print("最小储能容量(kWh):", br["best"]["bess_kwh"])
            print("覆盖率:", br["best"]["cover_ratio"])


if __name__ == "__main__":
    main()
