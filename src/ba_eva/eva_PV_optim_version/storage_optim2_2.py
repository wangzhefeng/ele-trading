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
# 单位配置（工程强烈推荐）
# ============================================================
@dataclass
class UnitsConfig:
    """
    内部统一单位：
      功率: kW
      电量: kWh
      时间: hour
    """
    load_power: str = "kW"     # kW / MW
    pv_power: str = "kW"       # kW/kWp（unit curve）
    wind_power: str = "MW"     # MW / kW


# ============================================================
# 全量规划配置 cfg
# ============================================================
@dataclass
class PlanConfigFast:
    # ---------- 成本 ----------
    pv_capex_yuan_per_kwp: float = 2000.0
    bess_capex_yuan_per_kwh: float = 1000.0

    # ---------- 储能物理 ----------
    eta_roundtrip: float = 0.92
    c_rate: float = 0.5
    soc_init_frac: float = 0.5
    soc_min_frac: float = 0.1
    soc_max_frac: float = 1.0

    # ---------- 约束 ----------
    self_use_ratio_min: float = 0.6
    load_cover_ratio_min: float = 0.2

    constraint_mode: str = "annual"     # annual / monthly
    monthly_all_must_meet: bool = True

    # ---------- PV 搜索 ----------
    pv_min_kwp: float = 0.0
    pv_step_coarse_kwp: float = 2000.0
    pv_step_fine_kwp: float = 250.0
    pv_refine_window_kwp: float = 8000.0
    pv_max_kwp: Optional[float] = None

    # ---------- 储能搜索 ----------
    enable_bess: bool = True
    batt_hi_init_kwh: float = 500.0
    batt_hi_max_kwh: float = 1e7
    batt_bisect_iter: int = 26
    batt_tol_kwh: float = 1.0

    # ---------- 工程 ----------
    use_numba: bool = True


# ============================================================
# 工具函数
# ============================================================
def infer_dt_hours(t) -> float:
    """
    输入：
      t: pd.Series 或 DatetimeIndex（时间轴）

    输出：
      dt_hours: float
    """

    # ===== 统一转为 Series（关键）=====
    if isinstance(t, pd.DatetimeIndex):
        t = pd.Series(t)
    else:
        t = pd.Series(pd.to_datetime(t))

    t = t.sort_values().reset_index(drop=True)

    if len(t) < 2:
        raise ValueError("时间点数量不足，无法推断 dt")

    dt = t.diff().dropna().mode().iloc[0]
    return float(dt.total_seconds() / 3600.0)


def normalize_time_and_load(
    df: pd.DataFrame,
    time_col: str,
    load_col: str,
    units: UnitsConfig,
) -> Tuple[pd.Series, np.ndarray, list]:

    print("### normalize_time_and_load FIXED VERSION CALLED")
    warnings = []

    if load_col not in df.columns:
        raise KeyError(f"负荷列 {load_col} 不存在")

    # ---------- 时间轴 ----------
    if time_col in df.columns:
        t = pd.to_datetime(df[time_col]).copy()
    elif isinstance(df.index, pd.DatetimeIndex):
        t = pd.Series(pd.to_datetime(df.index), name="Time")
        warnings.append("使用 DatetimeIndex 作为时间轴")
    else:
        raise ValueError("未找到时间列，且 index 不是 DatetimeIndex")

    # ---------- 负荷 ----------
    load = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)

    # ---------- 单位换算 ----------
    if units.load_power.lower() == "mw":
        load = load * 1000.0

    # ---------- 排序（关键：只对 Series reset_index） ----------
    order = np.argsort(t.values)
    t = t.iloc[order].reset_index(drop=True)
    load = load[order]

    # ---------- 诊断 ----------
    if load.max() < 10:
        warnings.append("负荷峰值 <10kW，疑似单位错误")

    return t, load, warnings


def as_time_series(
    x: Union[pd.Series, pd.DataFrame],
    time_col: str,
    value_cols: Tuple[str, ...],
    scale: float,
) -> pd.Series:
    if isinstance(x, pd.Series):
        s = x.copy()
        s.index = pd.to_datetime(s.index)
        return pd.to_numeric(s, errors="coerce").fillna(0.0) * scale

    df = x.copy()
    if time_col in df.columns:
        t = pd.to_datetime(df[time_col])
        df = df.drop(columns=[time_col])
    else:
        t = pd.to_datetime(df.index)

    for c in value_cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
            s.index = t
            return s * scale

    raise ValueError("未找到数值列")


def align_to_time(t: pd.Series, s: pd.Series) -> np.ndarray:
    return (
        s.reindex(pd.DatetimeIndex(t))
        .interpolate("time")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )


# ============================================================
# 调度（PV + 风 + 储能）
# ============================================================
@njit
def dispatch_numba(load_kw, gen_kw, dt, batt_kwh, eta, c_rate, soc0, soc_min_f, soc_max_f):
    gen_e = used_e = load_e = bess_dis = 0.0

    if batt_kwh <= 0:
        for i in range(load_kw.shape[0]):
            L = max(load_kw[i], 0)
            G = max(gen_kw[i], 0)
            load_e += L * dt
            gen_e += G * dt
            used_e += min(L, G) * dt
        return gen_e, used_e, load_e, bess_dis

    soc = soc0 * batt_kwh
    soc_min = soc_min_f * batt_kwh
    soc_max = soc_max_f * batt_kwh
    pmax = c_rate * batt_kwh
    eta_c = eta ** 0.5
    eta_d = eta ** 0.5

    for i in range(load_kw.shape[0]):
        L = max(load_kw[i], 0)
        G = max(gen_kw[i], 0)
        load_e += L * dt
        gen_e += G * dt

        direct = min(L, G)
        used_e += direct * dt

        surplus = G - direct
        deficit = L - direct

        if surplus > 1e-9 and soc < soc_max:
            ch = min(surplus, pmax, (soc_max - soc) / dt)
            soc += ch * dt * eta_c

        if deficit > 1e-9 and soc > soc_min:
            dis = min(deficit, pmax, (soc - soc_min) * eta_d / dt)
            soc -= dis * dt / eta_d
            used_e += dis * dt
            bess_dis += dis * dt

    return gen_e, used_e, load_e, bess_dis


def evaluate(load_kw, gen_kw, dt, batt_kwh, cfg: PlanConfigFast) -> Dict[str, float]:
    if cfg.use_numba and NUMBA_OK:
        g, u, l, b = dispatch_numba(
            load_kw, gen_kw, dt, batt_kwh,
            cfg.eta_roundtrip, cfg.c_rate,
            cfg.soc_init_frac, cfg.soc_min_frac, cfg.soc_max_frac
        )
    else:
        g = gen_kw.sum() * dt
        l = load_kw.sum() * dt
        u = np.minimum(load_kw, gen_kw).sum() * dt
        b = 0.0

    return {
        "gen_kwh": g,
        "used_kwh": u,
        "load_kwh": l,
        "self_use_ratio": u / g if g > 1e-9 else 0.0,
        "load_cover_ratio": u / l if l > 1e-9 else 0.0,
        "bess_discharge_kwh": b,
    }


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
    # TODO 无用
    # cfg_pv = PlanConfigFastWind(
    #     # -------- 约束 --------
    #     self_use_ratio_min=0.60,
    #     load_cover_ratio_min=0.30,
    #     # -------- 成本 --------
    #     pv_capex_yuan_per_kwp=2000.0,
    #     bess_capex_yuan_per_kwh=1000.0,
    #     # -------- PV 搜索 --------
    #     pv_step_coarse_kwp=1000.0,
    #     pv_step_fine_kwp=250.0,
    #     pv_refine_window_kwp=8000.0,

    #     eta_roundtrip=0.92,
    #     c_rate=0.5,
    #     soc_init_frac=0.5,

    #     enable_bess=True,          # 需要储能就 True；只规划PV则 False
    #     batt_hi_init_kwh=1000.0,
    #     batt_bisect_iter=22,

    #     use_numba=True
    # )
    # ##############################
    # TODO 
    # ##############################
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
    # PV(Photo Voltaics) power data
    # ------------------------------
    pv_kw_28 = generate_pv_data(df=df_2025, lat=28.42, lon=117.88, capacity_kwp=28250)
    # ------------------------------
    # run
    # ------------------------------
    # 1. 确保时间列为 datetime
    df_2025["Time"] = pd.to_datetime(df_2025["Time"])
    df_wind["Time"] = pd.to_datetime(df_wind["Time"])

    # 2. 全部设为 Time 索引
    df_load = df_2025.set_index("Time")[["P_kw"]]
    df_wind = df_wind.set_index("Time")[["WindPower_MW"]]
    df_pv = pv_kw_28.to_frame(name="PV_kw")        # 若 pv_kw_28 是 Series
    # ------------------------------
    # config
    # ------------------------------
    cfg_ess = PlanConfigFast(
        self_use_ratio_min=0.6,
        load_cover_ratio_min=0.3,
        enable_bess=True,
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
    print(res_ess["pv_kwp"], res_ess["bess_kwh"], res_ess["debug"]["pv_profile_missing"])

if __name__ == "__main__":
    main()
