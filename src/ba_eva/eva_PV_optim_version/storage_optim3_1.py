# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim3_1.py
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
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, Union

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
try:
    from numba import njit
    NUMBA_OK = True
except Exception:
    NUMBA_OK = False
    def njit(*args, **kwargs):
        def deco(f): return f
        return deco

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
# from utils.log_util import logger


# ============================================================
# 1. 配置
# ============================================================

@dataclass
class BESSConfig:
    eta_charge: float = 0.92
    eta_discharge: float = 0.92
    soc_min: float = 0.10
    soc_max: float = 1.00
    soc_init: float = 0.50
    c_rate: float = 1.0


@dataclass
class Targets:
    min_wind_utilization: float = 0.60   # 风电消纳率
    min_load_coverage: float = 0.30      # 负荷覆盖率


# ============================================================
# 2. 数据读取 & 对齐
# ============================================================

def read_timeseries(obj: Union[str, pd.DataFrame]) -> pd.DataFrame:
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
    else:
        ext = os.path.splitext(obj.lower())[1]
        if ext == ".csv":
            df = pd.read_csv(obj)
        elif ext in [".xls", ".xlsx"]:
            df = pd.read_excel(obj)
        else:
            raise ValueError("Unsupported file type")

    if "Time" in df.columns:
        df["Time"] = pd.to_datetime(df["Time"])
        df = df.set_index("Time")
    elif isinstance(df.index, pd.DatetimeIndex):
        pass
    else:
        raise ValueError("Need Time column or DatetimeIndex")

    return df.sort_index()


def align_and_merge(
    df_load: pd.DataFrame,
    df_wind: pd.DataFrame,
    load_col_kw: str = "P_kw",
    wind_col_mw: str = "WindPower_MW",
    freq: Optional[str] = None,
):
    if freq is None:
        freq = pd.infer_freq(df_load.index) or "15min"

    idx = pd.date_range(
        max(df_load.index.min(), df_wind.index.min()),
        min(df_load.index.max(), df_wind.index.max()),
        freq=freq,
    )

    dfl = df_load[[load_col_kw]].rename(columns={load_col_kw: "Load_kW"}).reindex(idx).interpolate()
    dfw = df_wind[[wind_col_mw]].rename(columns={wind_col_mw: "Wind_MW"}).reindex(idx).interpolate()
    dfw["Wind_kW"] = dfw["Wind_MW"] * 1000
    dfw = dfw.drop(columns=["Wind_MW"])

    df = pd.concat([dfl, dfw], axis=1)
    dt_h = pd.to_timedelta(freq).total_seconds() / 3600
    return df, dt_h


# ============================================================
# 3. 弃电搬运调度（核心）
# ============================================================

def simulate_surplus_shift(
    load_kw, wind_kw, dt_h, cap_mwh, bess: BESSConfig
):
    cap_kwh = cap_mwh * 1000
    pmax = bess.c_rate * cap_kwh
    e = bess.soc_init * cap_kwh
    e_min = bess.soc_min * cap_kwh
    e_max = bess.soc_max * cap_kwh

    n = len(load_kw)
    soc = np.zeros(n)
    charge = np.zeros(n)
    discharge = np.zeros(n)
    served = np.zeros(n)
    curtail = np.zeros(n)

    for t in range(n):
        L = load_kw[t]
        W = wind_kw[t]

        served_direct = min(L, W)
        surplus = max(W - L, 0)
        deficit = max(L - W, 0)

        room = max(e_max - e, 0)
        avail = max(e - e_min, 0)

        ch = min(surplus, pmax, room / (bess.eta_charge * dt_h))
        dis = min(deficit, pmax, avail * bess.eta_discharge / dt_h)

        e += (ch * bess.eta_charge - dis / bess.eta_discharge) * dt_h
        e = np.clip(e, e_min, e_max)

        charge[t] = ch
        discharge[t] = dis
        served[t] = served_direct + dis
        curtail[t] = max(surplus - ch, 0)
        soc[t] = e / cap_kwh if cap_kwh > 0 else bess.soc_init

    E_load = load_kw.sum() * dt_h
    E_wind = wind_kw.sum() * dt_h
    E_served = served.sum() * dt_h

    return {
        "wind_util": E_served / E_wind if E_wind > 0 else 0,
        "load_cov": E_served / E_load if E_load > 0 else 0,
        "series": {
            "served": served,
            "charge": charge,
            "discharge": discharge,
            "curtail": curtail,
            "soc": soc,
        }
    }


# ============================================================
# 4. 最小容量搜索（二分）
# ============================================================

def find_min_capacity(
    load_kw, wind_kw, dt_h, bess, targets, cap_max=5000, tol=0.1
):
    # 可达性检查
    r_inf = simulate_surplus_shift(load_kw, wind_kw, dt_h, cap_mwh=1e6, bess=bess)
    if r_inf["wind_util"] < targets.min_wind_utilization or \
       r_inf["load_cov"] < targets.min_load_coverage:
        raise RuntimeError(
            f"目标在物理上不可达：\n"
            f"最大风电消纳率={r_inf['wind_util']:.3f}, "
            f"最大负荷覆盖率={r_inf['load_cov']:.3f}"
        )

    lo, hi = 0.0, 1.0
    while hi <= cap_max:
        r = simulate_surplus_shift(load_kw, wind_kw, dt_h, hi, bess)
        if r["wind_util"] >= targets.min_wind_utilization and \
           r["load_cov"] >= targets.min_load_coverage:
            break
        hi *= 2

    if hi > cap_max:
        raise RuntimeError("cap_max 不足")

    while hi - lo > tol:
        mid = (lo + hi) / 2
        r = simulate_surplus_shift(load_kw, wind_kw, dt_h, mid, bess)
        if r["wind_util"] >= targets.min_wind_utilization and \
           r["load_cov"] >= targets.min_load_coverage:
            hi = mid
        else:
            lo = mid

    return hi


# ============================================================
# 5. 主函数（你只调用这个）
# ============================================================
def run_wind_bess_planning_min_cap(
    load_file,
    wind_file,
    load_col_kw="P_kw",
    wind_col_mw="WindPower_MW",
    freq=None,
    cap_max_mwh=5000,
):

    bess = BESSConfig()
    targets = Targets()

    df_load = read_timeseries(load_file)
    df_wind = read_timeseries(wind_file)

    df, dt_h = align_and_merge(df_load, df_wind, load_col_kw, wind_col_mw, freq)

    load_kw = df["Load_kW"].values
    wind_kw = df["Wind_kW"].values

    cap = find_min_capacity(load_kw, wind_kw, dt_h, bess, targets, cap_max_mwh)

    res = simulate_surplus_shift(load_kw, wind_kw, dt_h, cap, bess)
    s = res["series"]

    schedule_df = pd.DataFrame({
        "Load_kW": load_kw,
        "Wind_kW": wind_kw,
        "Served_kW": s["served"],
        "Charge_kW": s["charge"],
        "Discharge_kW": s["discharge"],
        "Curtail_kW": s["curtail"],
        "SOC": s["soc"],
    }, index=df.index)

    return {
        "bess_capacity_mwh": cap,
        "wind_utilization": res["wind_util"],
        "load_coverage": res["load_cov"],
        "schedule_df": schedule_df,
    }




# 测试代码 main 函数
def main():
    from ba_eva.eva_PV_optim_version.data_loader import load_data
    from ba_eva.eva_PV_optim_version.wind_simu import generate_wind_data
    from ba_eva.eva_PV_optim_version.pv_simu import generate_pv_data
    # ------------------------------
    # 负荷数据
    # ------------------------------
    # df_2025 = pd.read_csv("D:\\228-售前测算\\乌兰察布\\df_2025.csv", encoding="utf_8_sig")
    df_2025 = load_data()
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401
    # ------------------------------
    # wind power data
    # ------------------------------
    df_wind = generate_wind_data(farm_capacity_mw=110.0, mean_wind_speed_140m=5.5, eq_full_load_hours=1920.7, lat=28.42, lon=117.88)
    # ------------------------------
    # 
    # ------------------------------
    res = run_wind_bess_planning_min_cap(
        load_file=df_2025,
        wind_file=df_wind,
        cap_max_mwh=3000,
    )

    print("储能容量(MWh):", res["bess_capacity_mwh"])
    print("风电消纳率:", res["wind_utilization"])
    print("负荷覆盖率:", res["load_coverage"])
    print(res["schedule_df"].head())

if __name__ == "__main__":
    main()
