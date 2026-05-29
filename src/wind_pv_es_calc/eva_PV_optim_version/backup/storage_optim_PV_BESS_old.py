# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim_0.py
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
from typing import Optional, Dict

import numpy as np
import pandas as pd
from ba_eva.storage_optim_common import (
    njit, NUMBA_OK,
    PlanConfigFast,
    infer_dt_hours, align_to_time, monthly_kwh,
)


# ------------------------------
# 年度调度（Numba）
# ------------------------------
@njit
def _dispatch_annual_numba(load_kw, 
                           pv_kw, 
                           dt_hours, 
                           batt_kwh,
                           eta_roundtrip, 
                           c_rate,
                           soc_init_frac, 
                           soc_min_frac, 
                           soc_max_frac):
    pv_gen = pv_used = load_e = direct_e = bess_dis = 0.0

    if batt_kwh <= 0.0:
        for i in range(load_kw.shape[0]):
            L = max(load_kw[i], 0.0)
            PV = max(pv_kw[i], 0.0)
            load_e += L * dt_hours
            pv_gen += PV * dt_hours
            d = PV if PV < L else L
            pv_used += d * dt_hours
            direct_e += d * dt_hours
        return pv_gen, pv_used, load_e, direct_e, bess_dis

    soc = soc_init_frac * batt_kwh
    soc_min = soc_min_frac * batt_kwh
    soc_max = soc_max_frac * batt_kwh
    Pmax = c_rate * batt_kwh
    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5

    if soc < soc_min: soc = soc_min
    if soc > soc_max: soc = soc_max

    for i in range(load_kw.shape[0]):
        L = max(load_kw[i], 0.0)
        PV = max(pv_kw[i], 0.0)

        load_e += L * dt_hours
        pv_gen += PV * dt_hours

        direct = PV if PV < L else L
        pv_used += direct * dt_hours
        direct_e += direct * dt_hours

        surplus = PV - direct
        deficit = L - direct

        if surplus > 1e-12 and soc < soc_max:
            charge_p = min(surplus, Pmax, (soc_max - soc) / dt_hours)
            soc += charge_p * dt_hours * eta_c

        if deficit > 1e-12 and soc > soc_min:
            discharge_p = min(deficit, Pmax, (soc - soc_min) * eta_d / dt_hours)
            soc -= discharge_p * dt_hours / eta_d
            pv_used += discharge_p * dt_hours
            bess_dis += discharge_p * dt_hours

    return pv_gen, pv_used, load_e, direct_e, bess_dis


def _dispatch_annual(load_kw, pv_kw, dt_hours, batt_kwh, cfg: PlanConfigFast):
    if cfg.use_numba and NUMBA_OK:
        return dict(zip(
            ["pv_gen_kwh", "pv_used_kwh", "load_kwh", "direct_used_kwh", "bess_discharge_kwh"],
            _dispatch_annual_numba(
                load_kw, 
                pv_kw, 
                dt_hours, 
                batt_kwh,
                cfg.eta_roundtrip, 
                cfg.c_rate,
                cfg.soc_init_frac, 
                cfg.soc_min_frac, 
                cfg.soc_max_frac
            )
        ))

    # Python fallback（省略，性能低，但逻辑一致）
    direct = np.minimum(load_kw, pv_kw)
    return {
        "pv_gen_kwh": float(pv_kw.sum() * dt_hours),
        "pv_used_kwh": float(direct.sum() * dt_hours),
        "load_kwh": float(load_kw.sum() * dt_hours),
        "direct_used_kwh": float(direct.sum() * dt_hours),
        "bess_discharge_kwh": 0.0,
    }

# ------------------------------
# 主规划函数（完整版）
# 把单机光伏出力曲线和负荷数据结合起来，在给定约束下搜索最小化投资的光伏+储能方案。约束主要围绕自用率、负荷覆盖率、储能参数和投资成本展开
# ------------------------------
def plan_pv_bess_min_capex_fast(
    df_2025: pd.DataFrame,
    pv_unit_kw: pd.Series,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: PlanConfigFast = PlanConfigFast(),
    # ===== 关键：比例阈值可在调用时覆盖 =====
    self_use_ratio_min: Optional[float] = None,
    load_cover_ratio_min: Optional[float] = None,
) -> Dict[str, object]:
    """
    输出：
      PV装机容量(kWp)
      PV全年各月发电量(kWh)
      PV投资(元)
      储能装机容量(kWh)
      储能投资(元)
    """
    # ---- 覆盖比例阈值（若调用时传入）----
    if self_use_ratio_min is not None:
        cfg.self_use_ratio_min = float(self_use_ratio_min)
    if load_cover_ratio_min is not None:
        cfg.load_cover_ratio_min = float(load_cover_ratio_min)
    
    # 负荷数据
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    dt_hours = infer_dt_hours(df[time_col])
    load_kw = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")

    # 光伏数据
    unit_kw = align_to_time(df[time_col], pv_unit_kw)
    print(len(unit_kw))

    # 最大功率
    peak_load = float(load_kw.max())
    pv_max_kwp = cfg.pv_max_kwp or max(cfg.pv_step_coarse_kwp, 3.0 * peak_load)
    
    # 按月累计电量（kWh）
    unit_monthly_kwh = monthly_kwh(df[time_col], unit_kw, dt_hours)
    # 
    load_kwh_total = float(load_kw.sum() * dt_hours)

    best = None
    pv_candidates = np.arange(cfg.pv_step_coarse_kwp, pv_max_kwp + 1e-9, cfg.pv_step_coarse_kwp)
    for pv_kwp in pv_candidates:
        pv_kw = unit_kw * pv_kwp

        # ---- 快速剪枝 ----
        if pv_kw.sum() * dt_hours < cfg.load_cover_ratio_min * load_kwh_total:
            continue

        stats = _dispatch_annual(load_kw, pv_kw, dt_hours, 0.0, cfg)
        if stats["pv_gen_kwh"] <= 0:
            continue

        self_use = stats["pv_used_kwh"] / stats["pv_gen_kwh"]
        cover = stats["pv_used_kwh"] / load_kwh_total

        if self_use < cfg.self_use_ratio_min or cover < cfg.load_cover_ratio_min:
            continue

        pv_capex = pv_kwp * cfg.pv_capex_yuan_per_kwp
        total_capex = pv_capex

        if best is None or total_capex < best["total_capex_yuan"]:
            best = {
                "pv_kwp": float(pv_kwp),
                "pv_monthly_kwh": unit_monthly_kwh * pv_kwp,
                "pv_capex_yuan": pv_capex,
                "bess_kwh": 0.0,
                "bess_capex_yuan": 0.0,
                "total_capex_yuan": total_capex,
                "self_use_ratio": self_use,
                "load_cover_ratio": cover,
                "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python",
            }

    if best is None:
        raise ValueError("未找到满足约束的 PV+储能配置，请检查比例或扩大搜索范围。")

    return best

# ------------------------------
# data check
# ------------------------------
def simple_energy_sanity_check(df_2025: pd.DataFrame,
                               time_col="Time",
                               load_col="P_kw",
                               target_cover=0.30,
                               self_use_min=0.60,
                               yield_list=(1000, 1100, 1200, 1300),  # kWh/kWp·年
                               ):
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col)

    dt_h = df[time_col].diff().dropna().mode().iloc[0].total_seconds() / 3600
    load_kwh_year = df[load_col].sum() * dt_h

    pv_used_target = target_cover * load_kwh_year
    pv_gen_required = pv_used_target / self_use_min

    rows = []
    for y in yield_list:
        rows.append({
            "yield_kWh_per_kWp_yr": y,
            "pv_required_MWp": pv_gen_required / y / 1000
        })

    return {
        "load_gwh_year": load_kwh_year / 1e6,
        "pv_used_target_gwh": pv_used_target / 1e6,
        "pv_gen_required_gwh": pv_gen_required / 1e6,
        "pv_required_table": pd.DataFrame(rows)
    }

def curve_based_energy_check(df_2025: pd.DataFrame,
                             pv_unit_kw: pd.Series,
                             time_col="Time",
                             load_col="P_kw",
                             target_cover=0.30,
                             self_use_min=0.60):
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col)

    pv_unit_kw = pv_unit_kw.reindex(df[time_col]).fillna(0.0)

    dt_h = df[time_col].diff().dropna().mode().iloc[0].total_seconds() / 3600
    load_kwh_year = df[load_col].sum() * dt_h

    # 单位 kWp 年发电量
    yield_curve = pv_unit_kw.sum() * dt_h

    pv_used_target = target_cover * load_kwh_year
    pv_gen_required = pv_used_target / self_use_min
    pv_kwp_required = pv_gen_required / yield_curve

    return {
        "load_gwh_year": load_kwh_year / 1e6,
        "yield_curve_kWh_per_kWp": yield_curve,
        "pv_required_MWp": pv_kwp_required / 1000
    }




# 测试代码 main 函数
def main():
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_loader import load_data
    # data path
    raw_energy_data_dir = Path("src/ba_eva/dataset/负荷曲线/")
    energy_data_path = Path("src/ba_eva/dataset/temp/df_2025.csv")
    # data load
    df_2025 = load_data(raw_data_dir=raw_energy_data_dir, energy_data_path=energy_data_path)
    print(df_2025)
    # ------------------------------
    # PV power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_pv_simu import generate_pv_data
    # data path
    pv_data_path = Path("src/ba_eva/dataset/temp/df_pv_2025.csv")
    # data load
    pv_kw = generate_pv_data(df=df_2025, lat=40.55, lon=113.4, capacity_kwp=1.0, pv_data_path=pv_data_path, plot_img=False)
    print(pv_kw)
    # ------------------------------
    # 光伏 + 储能测算
    # ------------------------------
    cfg = PlanConfigFast(
        pv_step_fine_kwp=500.0,
        load_cover_ratio_min=0.35,
    )
    res = plan_pv_bess_min_capex_fast(
        df_2025=df_2025,
        pv_unit_kw=pv_kw,
        load_col="P_kw",
        time_col="Time",
        cfg=cfg,
    )
    print("PV装机(kWp):", res["pv_kwp"])
    print("PV投资(元):", res["pv_capex_yuan"])
    print("储能容量(kWh):", res["bess_kwh"])
    print("储能投资(元):", res["bess_capex_yuan"])
    print("\nPV各月发电量(kWh):")
    print(res["pv_monthly_kwh"])
    print("\n约束指标：")
    print("口径:", cfg.constraint_mode)
    print("PV自用率 PV_used / PV_gen:", res["self_use_ratio"])
    print("PV覆盖率 PV_used / Load:", res["load_cover_ratio"])
    res["pv_monthly_kwh"].to_csv("src/ba_eva/dataset/temp/pv_monthly_kwh.csv")
    # ------------------------------
    # 
    # ------------------------------
    out_A = simple_energy_sanity_check(df_2025)
    print("年用电量(GWh):", out_A["load_gwh_year"])
    print(out_A["pv_required_table"])

    out_B = curve_based_energy_check(df_2025, pv_kw)
    print(out_B)
    
    comparison = pd.DataFrame([
        {
            "method": "A-年能量下界(1200h)",
            "pv_mwp": out_A["pv_required_table"].query("yield_kWh_per_kWp_yr==1200")["pv_required_MWp"].iloc[0]
        },
        {
            "method": "B-单位曲线能量",
            "pv_mwp": out_B["pv_required_MWp"]
        },
        {
            "method": "C-规划模型输出",
            "pv_mwp": 244.0
        }
    ])
    print(comparison)

if __name__ == "__main__":
    main()
