# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042014
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
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, Union

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from ba_eva.eva_PV_optim_version.storage_optim_common import (
    njit, NUMBA_OK,
    PlanConfigFast,
    infer_dt_hours, align_to_time, monthly_kwh,
)

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
# from utils.log_util import logger


# ==============================
# 基础工具
# ==============================
def _as_time_index_series(x: Union[pd.Series, pd.DataFrame], time_col: str, value_col_candidates: Tuple[str, ...], *, scale: float = 1.0) -> pd.Series:
    """
    将输入 x 规范为：Series(index=DatetimeIndex, values=float)
    - x 可为 Series (index为时间) 或 DataFrame(含 time_col + value_col)
    - value_col_candidates 依次尝试
    - scale 用于单位换算（例如 MW->kW）
    """
    if isinstance(x, pd.Series):
        s = x.copy()
        if not isinstance(s.index, pd.DatetimeIndex):
            # 尝试把 index 转成时间
            s.index = pd.to_datetime(s.index)
        s = pd.to_numeric(s, errors="coerce").fillna(0.0) * float(scale)
        return s

    if isinstance(x, pd.DataFrame):
        df = x.copy()
        if time_col not in df.columns:
            # 如果时间在 index
            if isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index().rename(columns={df.columns[0]: time_col})
            else:
                raise KeyError(f"wind/pv 输入 DataFrame 找不到时间列 {time_col}，且 index 不是 DatetimeIndex")

        df[time_col] = pd.to_datetime(df[time_col])
        vcol = None
        for c in value_col_candidates:
            if c in df.columns:
                vcol = c
                break
        if vcol is None:
            # 兜底：取除 time_col 外第一列
            cand = [c for c in df.columns if c != time_col]
            if not cand:
                raise KeyError(f"输入 DataFrame 除 {time_col} 外无数值列")
            vcol = cand[0]

        s = pd.to_numeric(df[vcol], errors="coerce").fillna(0.0)
        s.index = pd.to_datetime(df[time_col])
        s = s.sort_index() * float(scale)
        return s

    raise TypeError("输入必须是 pd.Series 或 pd.DataFrame")


# ==============================
# 年度调度（Numba / Python）
# ==============================
@njit
def _dispatch_annual_numba(
    load_kw, wind_kw, pv_kw, other_kw,
    dt_hours, batt_kwh,
    eta_roundtrip, c_rate,
    soc_init_frac, soc_min_frac, soc_max_frac
):
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

        # charge
        if surplus > 1e-9 and E > 0.0 and soc < soc_max:
            p_ch = surplus
            if p_ch > Pmax:
                p_ch = Pmax
            max_ch = (soc_max - soc) / dt_hours
            if p_ch > max_ch:
                p_ch = max_ch
            soc += p_ch * dt_hours * eta_c

        # discharge
        if deficit > 1e-9 and E > 0.0 and soc > soc_min:
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


def _dispatch_annual(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    batt_kwh: float,
    cfg: PlanConfigFast
) -> Dict[str, float]:
    if cfg.use_numba and NUMBA_OK:
        gen_e, used_e, load_e, direct_e, bess_dis = _dispatch_annual_numba(
            load_kw, wind_kw, pv_kw, other_kw,
            dt_hours, float(batt_kwh),
            float(cfg.eta_roundtrip), float(cfg.c_rate),
            float(cfg.soc_init_frac), float(cfg.soc_min_frac), float(cfg.soc_max_frac)
        )
    else:
        # Python fallback（矢量化 + 简化电池）
        gen_kw = wind_kw + pv_kw + other_kw
        direct_kw = np.minimum(load_kw, np.maximum(gen_kw, 0.0))
        gen_e = float(np.maximum(gen_kw, 0.0).sum() * dt_hours)
        load_e = float(np.maximum(load_kw, 0.0).sum() * dt_hours)
        used_e = float(direct_kw.sum() * dt_hours)
        direct_e = used_e
        bess_dis = 0.0
        # fallback 不模拟电池，主要用于无 numba 环境的可运行性

    return {
        "ren_gen_kwh": float(gen_e),
        "ren_used_kwh": float(used_e),
        "load_kwh": float(load_e),
        "direct_used_kwh": float(direct_e),
        "bess_discharge_kwh": float(bess_dis),
    }

# ==============================
# 电池二分：给定 PV，找最小 BESS
# ==============================
def _find_min_bess_kwh(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    cfg: PlanConfigFast
) -> Optional[Tuple[float, Dict[str, float]]]:
    """
    返回 (bess_kwh, stats)；若找不到可行解返回 None
    """
    def feasible(batt_kwh: float) -> Tuple[bool, Dict[str, float]]:
        st = _dispatch_annual(load_kw, wind_kw, pv_kw, other_kw, dt_hours, batt_kwh, cfg)
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

# ==============================
# 主规划函数（完整版）
# ==============================
def plan_wind_fixed_pv_bess_fast_full(
    df_2025: pd.DataFrame,
    pv_unit_kw: Union[pd.Series, pd.DataFrame],
    wind_input: Union[pd.Series, pd.DataFrame],
    *,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: PlanConfigFast = PlanConfigFast(),
    # 预留：其他新能源输入（可空）
    other_input: Optional[Union[pd.Series, pd.DataFrame]] = None,
    other_unit: str = "kW",  # 预留
    # wind 输入单位声明
    wind_unit: str = "MW",   # 你通常是 WindPower_MW
    # pv_unit 是否已经是 kW/kWp（默认是）
) -> Dict[str, Any]:

    # ---- 负荷 ----
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)

    dt_hours = infer_dt_hours(df[time_col])
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)

    # ---- 风电：支持 DataFrame/Series，自动取 WindPower_MW 或 wind_kw ----
    wind_scale = 1000.0 if wind_unit.lower() == "mw" else 1.0
    wind_s = _as_time_index_series(
        wind_input,
        time_col=time_col,
        value_col_candidates=("WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower"),
        scale=wind_scale,
    )
    wind_kw_arr = align_to_time(df[time_col], wind_s)

    # ---- PV 单位出力：支持 Series/DataFrame（按 index 或 Time 列）
    pv_unit_s = _as_time_index_series(
        pv_unit_kw,
        time_col=time_col,
        value_col_candidates=("pv_unit_kw", "pv_kw", "u", "value"),
        scale=1.0,
    )
    pv_unit_arr = align_to_time(df[time_col], pv_unit_s)

    # ---- 其他新能源预留 ----
    if other_input is None:
        other_kw_arr = np.zeros_like(load_kw_arr, dtype=np.float64)
    else:
        other_scale = 1.0 if other_unit.lower() == "kw" else 1000.0
        other_s = _as_time_index_series(
            other_input,
            time_col=time_col,
            value_col_candidates=("other_kw", "OtherPower_kW", "OtherPower"),
            scale=other_scale,
        )
        other_kw_arr = align_to_time(df[time_col], other_s)

    # ---- 基础量 ----
    load_kwh_total = float(load_kw_arr.sum() * dt_hours)
    wind_kwh_total = float(wind_kw_arr.sum() * dt_hours)

    peak_load = float(load_kw_arr.max()) if len(load_kw_arr) else 0.0
    pv_max_kwp = cfg.pv_max_kwp or max(cfg.pv_step_coarse_kwp, 3.0 * peak_load)

    # 年/月风电发电量输出
    windmonthly_kwh = monthly_kwh(df[time_col], wind_kw_arr, dt_hours)

    # ==========================
    # PV 搜索：粗扫 + 可选细扫
    # ==========================
    best = None
    best_pv_kwp_coarse = None

    pv_candidates = np.arange(cfg.pv_min_kwp, pv_max_kwp + 1e-9, cfg.pv_step_coarse_kwp)
    for pv_kwp in pv_candidates:
        pv_kw_arr = pv_unit_arr * float(pv_kwp)
        pv_kwh = float(pv_kw_arr.sum() * dt_hours)

        # --- 快速能量剪枝：风+光至少要到覆盖阈值的“理论上限”
        if (wind_kwh_total + pv_kwh) < cfg.load_cover_ratio_min * load_kwh_total:
            continue

        # --- 可行性判断：无电池或带电池
        if cfg.enable_bess:
            found = _find_min_bess_kwh(load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, dt_hours, cfg)
            if found is None:
                continue
            bess_kwh, st = found
        else:
            st = _dispatch_annual(load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, dt_hours, 0.0, cfg)
            if st["ren_gen_kwh"] <= 1e-9:
                continue
            self_use = st["ren_used_kwh"] / st["ren_gen_kwh"]
            cover = st["ren_used_kwh"] / st["load_kwh"]
            if (self_use < cfg.self_use_ratio_min) or (cover < cfg.load_cover_ratio_min):
                continue
            st["self_use_ratio"] = self_use
            st["load_cover_ratio"] = cover
            bess_kwh = 0.0

        pv_capex = float(pv_kwp) * cfg.pv_capex_yuan_per_kwp
        bess_capex = float(bess_kwh) * cfg.bess_capex_yuan_per_kwh
        total_capex = pv_capex + bess_capex

        rec = {
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

        if (best is None) or (total_capex < best["total_capex_yuan"]):
            best = rec
            best_pv_kwp_coarse = float(pv_kwp)

    if best is None:
        raise ValueError("未找到满足约束的方案：请扩大 pv_max_kwp 或放宽比例阈值。")

    # ---- 可选：细扫（在粗扫最优附近）
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
                found = _find_min_bess_kwh(load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, dt_hours, cfg)
                if found is None:
                    continue
                bess_kwh, st = found
            else:
                st = _dispatch_annual(load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, dt_hours, 0.0, cfg)
                if st["ren_gen_kwh"] <= 1e-9:
                    continue
                self_use = st["ren_used_kwh"] / st["ren_gen_kwh"]
                cover = st["ren_used_kwh"] / st["load_kwh"]
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
    pvmonthly_kwh = monthly_kwh(df[time_col], pv_gen_kw_arr, dt_hours)

    out = {
        "pv_kwp": best["pv_kwp"],
        "bess_kwh": best["bess_kwh"],
        "self_use_ratio": best["self_use_ratio"],
        "load_cover_ratio": best["load_cover_ratio"],

        "pv_gen_kwh_annual": pv_gen_kwh_total,
        "pv_gen_kwh_monthly": pvmonthly_kwh,

        "wind_gen_kwh_annual": wind_kwh_total,
        "wind_gen_kwh_monthly": windmonthly_kwh,

        "pv_capex_yuan": best["pv_capex_yuan"],
        "bess_capex_yuan": best["bess_capex_yuan"],
        "total_capex_yuan": best["total_capex_yuan"],

        "engine": best["engine"],
        "debug": {
            "ren_gen_kwh": best["ren_gen_kwh"],
            "ren_used_kwh": best["ren_used_kwh"],
            "direct_used_kwh": best["direct_used_kwh"],
            "bess_discharge_kwh": best["bess_discharge_kwh"],
            "dt_hours": dt_hours,
            "pv_max_kwp_used": pv_max_kwp,
        }
    }
    return out




# 测试代码 main 函数
def main():
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("src/ba_eva/dataset/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path)
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401
    print(df_2025)
    # ------------------------------
    # wind power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("src/ba_eva/dataset/temp/df_wind_2026.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=110.0, 
        mean_wind_speed_140m=5.5, 
        eq_full_load_hours=1920.7, 
        lat=28.42, 
        lon=117.88, 
        wind_data_path=wind_data_path
    )
    print(df_wind)
    # ------------------------------
    # PV(Photo Voltaics) power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_pv_simu import generate_pv_data
    pv_data_path = Path("src/ba_eva/dataset/temp/df_pv_2025.csv")
    pv_kw_0 = generate_pv_data(
        df=df_2025, 
        lat=28.42, 
        lon=117.88, 
        capacity_kwp=1, 
        pv_data_path=pv_data_path, 
        plot_img=False
    )
    print(pv_kw_0)
    # ------------------------------
    # config
    # ------------------------------
    # cfg = PlanConfigFast(
    #     # -------- 约束 --------
    #     self_use_ratio_min=0.60,     # 新能源整体自用率 ≥ 60%
    #     load_cover_ratio_min=0.35,   # 新能源覆盖负荷 ≥ 30%
    #
    #     # -------- PV 搜索 --------
    #     pv_step_coarse_kwp=1000.0,   # PV 扫描步长（2MWp）
    #     pv_max_kwp=None,             # None => 自动 = max(step, 3*peak_load)
    #
    #     # -------- 成本 --------
    #     pv_capex_yuan_per_kwp=2000.0,
    #     bess_capex_yuan_per_kwh=1000.0,
    #
    #     # -------- 储能参数 --------
    #     eta_roundtrip=0.92,
    #     c_rate=0.5,
    #     soc_init_frac=0.5,
    #
    #     # -------- 性能 --------
    #     use_numba=True,              # 有 numba 就会自动启用
    # )
    cfg_pv = PlanConfigFast(
        load_cover_ratio_min=0.30,
        pv_step_coarse_kwp=1000.0,
        batt_hi_init_kwh=1000.0,
        batt_bisect_iter=22,
        batt_tol_kwh=100.0,
    )
    res = plan_wind_fixed_pv_bess_fast_full(
        df_2025=df_2025,
        pv_unit_kw=pv_kw_0,      # Series: kW/kWp, index=Time
        wind_input=df_wind,      # DataFrame: [Time, WindPower_MW] 或 Series(kW)
        load_col="P_kw",
        time_col="Time",
        cfg=cfg_pv,
        other_input=None,        # 预留其他新能源
        wind_unit="MW",
    )
    print("PV(kWp):", res["pv_kwp"])
    print("BESS(kWh):", res["bess_kwh"])
    print("新能源自用率:", res["self_use_ratio"])
    print("负荷覆盖率:", res["load_cover_ratio"])

    print("\nPV 年发电量(kWh):", res["pv_gen_kwh_annual"])
    print("Wind 年发电量(kWh):", res["wind_gen_kwh_annual"])

    print("\nPV 月发电量(kWh):")
    print(res["pv_gen_kwh_monthly"])

    print("\nWind 月发电量(kWh):")
    print(res["wind_gen_kwh_monthly"])

    print("\n投资(元): PV / BESS / Total")
    print(res["pv_capex_yuan"], res["bess_capex_yuan"], res["total_capex_yuan"])

    print("\nengine:", res["engine"])
    print("debug:", res["debug"])
    
    print(res.keys())

    res["pv_gen_kwh_monthly"].to_csv("src/ba_eva/dataset/temp/pv_gen_kwh_monthly.csv")
    res["wind_gen_kwh_monthly"].to_csv("src/ba_eva/dataset/tempwind_gen_kwh_monthly.csv")

if __name__ == "__main__":
    main()
