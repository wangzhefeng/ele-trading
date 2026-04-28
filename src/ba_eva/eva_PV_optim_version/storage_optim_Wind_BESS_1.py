# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim2.py
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
from ba_eva.eva_PV_optim_version.storage_optim_common import (
    njit, NUMBA_OK,
    UnitsConfig, PlanConfigFast,
    infer_dt_hours, normalize_time_and_load, as_time_series, align_to_time,
    dispatch_numba, evaluate,
)

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
# from utils.log_util import logger


# ============================================================
# 主规划函数（最终版）
# ============================================================
def plan_energy_system(
    df_load: pd.DataFrame,
    *,
    pv_unit_kw: Optional[Union[pd.Series, pd.DataFrame]] = None,
    wind_input: Optional[Union[pd.Series, pd.DataFrame]] = None,
    time_col: str = "Time",
    load_col: str = "P_kw",
    cfg: PlanConfigFast = PlanConfigFast(),
    units: UnitsConfig = UnitsConfig(),
) -> Dict[str, Any]:

    # ---------- 负荷 ----------
    # try:
    #     t, load_kw, load_warn = normalize_time_and_load(
    #         df_load, time_col, load_col, units
    #     )
    # except Exception as e:
    #     return {"feasible": False, "diagnosis": {"reason": "INVALID_LOAD", "msg": str(e)}}
    t, load_kw, load_warn = normalize_time_and_load(df_load, time_col, load_col, units)
    dt = infer_dt_hours(t)

    # ---------- 风 ----------
    wind_kw = np.zeros_like(load_kw)
    if wind_input is not None:
        scale = 1000.0 if units.wind_power.lower() == "mw" else 1.0
        w = as_time_series(
            wind_input, time_col,
            ("WindPower_MW", "wind_mw", "wind_kw"),
            scale
        )
        wind_kw = align_to_time(t, w)

    # ---------- PV ----------
    pv_kw = np.zeros_like(load_kw)
    if pv_unit_kw is not None:
        pu = as_time_series(
            pv_unit_kw, time_col,
            ("pv_unit_kw", "pv_kw", "value"),
            1.0
        )
        pv_kw = align_to_time(t, pu)

    # ---------- 总新能源 ----------
    gen_kw = wind_kw + pv_kw

    # ---------- 仅储能场景 ----------
    if pv_unit_kw is None and wind_input is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_GENERATION",
                "message": "无 PV / 无风，仅储能无法创造能量，仅可做移峰套利"
            }
        }

    # ---------- 储能搜索 ----------
    best = None
    for batt in np.linspace(0, cfg.batt_hi_max_kwh, 40):
        stats = evaluate(load_kw, gen_kw, dt, batt, cfg)
        if (
            stats["self_use_ratio"] >= cfg.self_use_ratio_min and
            stats["load_cover_ratio"] >= cfg.load_cover_ratio_min
        ):
            cost = batt * cfg.bess_capex_yuan_per_kwh
            if best is None or cost < best["cost"]:
                best = {"bess_kwh": batt, "metrics": stats, "cost": cost}

    if best is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_FEASIBLE_SOLUTION",
                "self_use_ratio_min": cfg.self_use_ratio_min,
                "load_cover_ratio_min": cfg.load_cover_ratio_min,
            }
        }

    return {
        "feasible": True,
        "solution": best,
        "warnings": load_warn,
        "context": {
            "dt_hours": dt,
            "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python",
        }
    }




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
    # TODO 未使用 PV power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_pv_simu import generate_pv_data
    pv_data_path = Path("src/ba_eva/dataset/temp/df_pv_2025.csv")
    pv_kw_28 = generate_pv_data(
        df=df_2025, 
        lat=28.42, 
        lon=117.88, 
        capacity_kwp=28250, 
        pv_data_path=pv_data_path, 
        plot_img=False
    )
    print(pv_kw_28)
    # ------------------------------
    # run
    # ------------------------------
    # 1. 确保时间列为 datetime
    df_2025["Time"] = pd.to_datetime(df_2025["Time"])
    df_wind["Time"] = pd.to_datetime(df_wind["Time"])

    # 2. 全部设为 Time 索引
    df_load = df_2025.set_index("Time")[["P_kw"]]
    df_wind = df_wind.set_index("Time")[["WindPower_MW"]]
    # TODO
    df_pv = pv_kw_28.to_frame(name="PV_kw")  # 若 pv_kw_28 是 Series
    # ------------------------------
    # config
    # ------------------------------
    cfg_ess = PlanConfigFast(
        load_cover_ratio_min=0.3,
        batt_hi_max_kwh=2e5,
    )
    units = UnitsConfig(
        load_power="kW",
        wind_power="MW",
    )
    res_ess = plan_energy_system(
        df_load=df_load,
        wind_input=df_wind,
        time_col="Time",
        load_col="P_kw",
        cfg=cfg_ess,
        units=units,
    )
    print(res_ess)
    # TODO print(res_ess["pv_kwp"], res_ess["bess_kwh"], res_ess["debug"]["pv_profile_missing"])

if __name__ == "__main__":
    main()
