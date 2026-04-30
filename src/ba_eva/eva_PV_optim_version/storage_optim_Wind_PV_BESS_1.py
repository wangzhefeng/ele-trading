# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim1.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042017
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd
from src.ba_eva.eva_PV_optim_version.storage_optim_common import (
    BESSConfig, 
    ensure_time_sorted
)


def align_curves(df_load: pd.DataFrame,
                 df_pv: pd.DataFrame,
                 df_wind: pd.DataFrame,
                 load_col: str,
                 pv_col: str,
                 wind_col: str,
                 time_col: str = "Time") -> pd.DataFrame:
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


def energy_gate_check(df_mix: pd.DataFrame, freq: str = "15T", target_ratio: float = 0.30) -> Dict[str, Any]:
    """
    先判断：发电量(风+光) / 用电量 是否 >= target_ratio
    仅能量层面，不考虑弃电与储能。
    """
    dt_h = pd.to_timedelta(freq).total_seconds() / 3600.0
    # 负荷电能量
    load_kwh = float(df_mix["load_kw"].sum() * dt_h)
    # 发电电能量
    gen_kwh = float(df_mix["gen_kw"].sum() * dt_h)
    # 发电量/负荷用电量
    ratio = 0.0 if load_kwh <= 0 else gen_kwh / load_kwh

    return {
        "load_total_kwh": load_kwh,
        "gen_total_kwh": gen_kwh,
        "gen_ratio": ratio,
        "pass_gate": (ratio >= target_ratio)
    }


def simulate_bess_for_coverage(df_mix: pd.DataFrame, bess_kwh: float, cfg: BESSConfig) -> Dict[str, Any]:
    """
    贪心调度：最大化可再生对负荷的供给。
    充电仅来自 surplus=gen-load（弃电候选）。
    放电仅供 deficit=load-gen，且不超过负荷缺口。
    充放切换间隔 switch_gap_hours。
    0.5C: Pmax = E/hours_to_full
    """
    df = df_mix.copy()
    dt_h = pd.to_timedelta(cfg.freq).total_seconds() / 3600.0
    gap_steps = int(round(cfg.switch_gap_hours / dt_h))

    cap_kwh = float(max(bess_kwh, 0.0))
    pmax_kw = 0.0 if cap_kwh <= 0 else cap_kwh / cfg.hours_to_full

    e = cap_kwh * cfg.soc_init
    e_min = cap_kwh * cfg.soc_min
    e_max = cap_kwh * cfg.soc_max

    last_action = 0
    last_action_t = -10**9

    direct_used_kwh = 0.0
    discharge_used_kwh = 0.0
    curtailed_kwh = 0.0

    load_total_kwh = float(df["load_kw"].sum() * dt_h)

    for i in range(len(df)):
        load = float(df.at[i, "load_kw"])
        gen = float(df.at[i, "gen_kw"])

        direct = min(load, gen)
        direct_used_kwh += direct * dt_h

        surplus = max(gen - load, 0.0)
        deficit = max(load - gen, 0.0)

        can_charge = not (last_action == -1 and (i - last_action_t) < gap_steps)
        can_discharge = not (last_action == +1 and (i - last_action_t) < gap_steps)

        # charge
        p_ch = 0.0
        if surplus > 0 and cap_kwh > 0 and can_charge:
            p_ch_cap = (e_max - e) / max(dt_h * cfg.eta_charge, 1e-9)
            p_ch = min(surplus, pmax_kw, p_ch_cap)
            if p_ch > 1e-9:
                e += p_ch * dt_h * cfg.eta_charge
                last_action, last_action_t = +1, i

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


def estimate_min_bess_capacity(df_mix: pd.DataFrame,
                               cfg: BESSConfig,
                               target_cover_ratio: float = 0.30,
                               cap_high_kwh: Optional[float] = None, 
                               max_iter: int = 28) -> Dict[str, Any]:
    """
    二分搜索最小 BESS 容量，使 cover_ratio >= target_cover_ratio
    """
    # 无需储能
    baseline = simulate_bess_for_coverage(df_mix, 0.0, cfg)
    if baseline["cover_ratio"] >= target_cover_ratio:
        return {
            "status": "no_bess_needed", 
            "baseline": baseline, 
            "best": baseline
        }

    # TODO 补充注释
    if cap_high_kwh is None:
        surplus_kw = np.maximum(df_mix["gen_kw"].values - df_mix["load_kw"].values, 0.0)
        cap_high_kwh = max(1_000.0, float(np.max(surplus_kw)) * 6.0)
    # 储能容量上下界
    lo, hi = 0.0, float(cap_high_kwh)
    # 扩边界直到可行
    res_hi = simulate_bess_for_coverage(df_mix, hi, cfg)
    expand = 0
    while res_hi["cover_ratio"] < target_cover_ratio and expand < 12:
        hi *= 2.0
        res_hi = simulate_bess_for_coverage(df_mix, hi, cfg)
        expand += 1

    if res_hi["cover_ratio"] < target_cover_ratio:
        return {
            "status": "not_reachable", 
            "baseline": baseline, 
            "try_hi": res_hi
        }

    best = res_hi
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        res_mid = simulate_bess_for_coverage(df_mix, mid, cfg)

        if res_mid["cover_ratio"] >= target_cover_ratio:
            best = res_mid
            hi = mid
        else:
            lo = mid

        if (hi - lo) / max(hi, 1.0) < 0.01:
            break

    return {
        "status": "ok", 
        "baseline": baseline, 
        "best": best
    }


def evaluate_50wind_50pv_with_bess(df_2025: pd.DataFrame,
                                   pv_kw_50m: pd.DataFrame,
                                   df_wind: pd.DataFrame,
                                   load_col: str = "P_kw",
                                   pv_col: str = "pv_kw",
                                   wind_col: str = "WindPower_MW",
                                   time_col: str = "Time",
                                   target_ratio: float = 0.30,
                                   cfg: Optional[BESSConfig] = None) -> Dict[str, Any]:
    """
    先能量门槛：gen/load >= 30% 才做储能评估
    """
    # 负荷、光电、风电、总发电
    df_mix = align_curves(
        df_load=df_2025,
        df_pv=pv_kw_50m,
        df_wind=df_wind,
        load_col=load_col,
        pv_col=pv_col,
        wind_col=wind_col,
        time_col=time_col,
    )
    # 先判断：发电量(风+光) / 用电量 是否 >= target_ratio
    gate = energy_gate_check(df_mix, freq=cfg.freq, target_ratio=target_ratio)
    
    # 输出
    out = {
        "df_mix": df_mix,
        "gate": gate,
        "status": "gate_failed" if not gate["pass_gate"] else "gate_passed"
    }

    # 门槛不过：不评估储能
    if not gate["pass_gate"]:
        out["message"] = (
            f"能量门槛未通过：风+光年发电量占比={gate['gen_ratio']:.3f}，"
            f"低于目标{target_ratio:.2f}。在只允许用风光充电的前提下，储能无法把覆盖率提高到30%。"
        )
        return out
    # 门槛通过：再做储能评估（以实现“实际覆盖率>=30%”）
    else:
        bess_res = estimate_min_bess_capacity(
            df_mix=df_mix,
            cfg=cfg,
            target_cover_ratio=target_ratio,
        )
        out["bess_result"] = bess_res
        return out




# 测试代码 main 函数
def main():
    # ##############################
    # model 1: 风光固定条件下，储能寻优
    # ##############################
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("src/ba_eva/dataset/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path)
    print(df_2025)
    # ------------------------------
    # wind power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("src/ba_eva/dataset/temp/df_wind_2026.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=200.0, 
        mean_wind_speed_140m=7.27, 
        eq_full_load_hours=2590, 
        lat=40.55, 
        lon=113.4, 
        wind_data_path=wind_data_path
    )
    print(df_wind)
    # ------------------------------
    # PV power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_pv_simu import generate_pv_data
    pv_data_path = Path("src/ba_eva/dataset/temp/df_pv_2025.csv")
    pv_kw_50m = generate_pv_data(
        df=df_2025, 
        lat=40.55, 
        lon=113.4, 
        capacity_kwp=100000.0, 
        pv_data_path=pv_data_path, 
        plot_img=False
    )
    print(pv_kw_50m)
    # ------------------------------
    # run
    # ------------------------------
    # config
    cfg = BESSConfig(soc_min=0.0)
    # evaluate
    res = evaluate_50wind_50pv_with_bess(
        df_2025=df_2025,
        pv_kw_50m=pv_kw_50m,
        df_wind=df_wind,
        load_col="P_kw",
        pv_col="pv_kw",
        wind_col="WindPower_MW",
        time_col="Time",
        target_ratio=0.30,
        cfg=cfg
    )
    print("状态:", res["status"])
    print("年负荷(kWh):", res["gate"]["load_total_kwh"])
    print("年发电(kWh):", res["gate"]["gen_total_kwh"])
    print("发电/负荷比例:", res["gate"]["gen_ratio"])

    if res["status"] == "gate_passed":
        br = res["bess_result"]
        print("\n储能评估状态:", br["status"])
        print("无电池覆盖率:", br["baseline"]["cover_ratio"])
        if br["status"] == "ok":
            best = br["best"]
            print("\n满足30%覆盖的最小储能：")
            print("  容量(kWh):", best["bess_kwh"])
            print("  功率上限(kW,0.5C):", best["pmax_kw"])
            print("  覆盖率:", best["cover_ratio"])
            print("  弃电(kWh):", best["curtailed_kwh"])
    else:
        print(res["message"])
    # ------------------------------
    # 负荷 - 风电
    # ------------------------------
    df_2025_ = df_2025.copy()
    df_2025_["P_kw"] = df_2025_["P_kw"] - df_wind["WindPower_MW"]
    print(df_2025_[df_2025_["P_kw"] < 0])
    print(df_2025_)

if __name__ == "__main__":
    main()
