# -*- coding: utf-8 -*-

# ***************************************************
# * File        : best_wind_pv_storage.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-11
# * Version     : 1.0.051114
# * Description : 风光储最佳组合测算¶
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
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, Union
import calendar
from datetime import datetime
import copy
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from ba_eva.storage_optim_common import (
    NUMBA_OK, njit
)

# ============================================================
# 调试工具
# ============================================================
def dbg(msg: str, obj: Any = None):
    print(f"[DBG] {msg}")
    if obj is not None:
        try:
            print("      type:", type(obj))
            if isinstance(obj, (pd.Series, pd.Index, pd.DatetimeIndex)):
                print("      len :", len(obj))
                print("      head:", list(obj[:3]))
            elif isinstance(obj, pd.DataFrame):
                print("      shape:", obj.shape)
                print("      columns:", list(obj.columns))
                print("      index type:", type(obj.index))
        except Exception as e:
            print("      (dbg failed):", e)


# ============================================================
# 单位配置
# ============================================================
@dataclass
class UnitsConfig:
    load_power: str = "kW"     # kW / MW
    pv_power: str = "kW"       # kW
    wind_power: str = "MW"     # MW / kW


# ============================================================
# 全量规划配置
# ============================================================
@dataclass
class PlanConfigFast:
    bess_capex_yuan_per_kwh: float = 1000.0

    eta_roundtrip: float = 0.92
    c_rate: float = 0.5
    soc_init_frac: float = 0.5
    soc_min_frac: float = 0.1
    soc_max_frac: float = 1.0

    self_use_ratio_min: float = 0.6
    load_cover_ratio_min: float = 0.2

    batt_hi_max_kwh: float = 20000.0
    use_numba: bool = True


# ============================================================
# 时间工具（100% 防 Index）
# ============================================================
def infer_dt_hours(t) -> float:
    dbg("infer_dt_hours input", t)

    t = pd.Series(pd.to_datetime(t), name="Time")
    t = t.sort_values().reset_index(drop=True)

    if len(t) < 2:
        raise ValueError("时间点数量不足")

    dt = t.diff().dropna().mode().iloc[0]
    dt_hours = dt.total_seconds() / 3600.0
    dbg(f"infer_dt_hours dt_hours={dt_hours}")
    return dt_hours


# ============================================================
# 负荷 + 时间轴规范化（终极安全版）
# ============================================================
def normalize_time_and_load(
    df: pd.DataFrame,
    time_col: str,
    load_col: str,
    units: UnitsConfig,
) -> Tuple[pd.Series, np.ndarray, list]:

    print("### normalize_time_and_load CALLED")
    warnings = []

    dbg("input df", df)

    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df_load 类型错误：{type(df)}")

    if load_col not in df.columns:
        raise KeyError(f"负荷列 {load_col} 不存在")

    # ---------- 时间轴 ----------
    if time_col in df.columns:
        raw_t = df[time_col]
        dbg("raw time_col", raw_t)
        t = pd.Series(pd.to_datetime(raw_t), name="Time")
    elif isinstance(df.index, pd.DatetimeIndex):
        t = pd.Series(pd.to_datetime(df.index), name="Time")
        warnings.append("使用 DatetimeIndex 作为时间轴")
    else:
        raise ValueError("未找到时间列")

    assert isinstance(t, pd.Series), f"t 类型异常：{type(t)}"

    # ---------- 负荷 ----------
    load = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)

    if units.load_power.lower() == "mw":
        load *= 1000.0

    # ---------- 排序 ----------
    order = np.argsort(t.values)
    t = t.iloc[order].reset_index(drop=True)
    load = load[order]

    dbg("normalized t", t)
    dbg("normalized load", load[:5])

    return t, load, warnings


# ============================================================
# 发电输入规范化
# ============================================================
def as_time_series(
    x: Union[pd.Series, pd.DataFrame],
    time_col: str,
    value_cols: Tuple[str, ...],
    scale: float,
) -> pd.Series:

    dbg("as_time_series input", x)

    if isinstance(x, pd.Series):
        s = pd.to_numeric(x, errors="coerce").fillna(0.0)
        s.index = pd.to_datetime(s.index)
        return s * scale

    if not isinstance(x, pd.DataFrame):
        raise TypeError("输入必须是 Series 或 DataFrame")

    if time_col in x.columns:
        t = pd.to_datetime(x[time_col])
        df = x.drop(columns=[time_col])
    else:
        t = pd.to_datetime(x.index)
        df = x.copy()

    for c in value_cols:
        if c in df.columns:
            s = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
            s.index = t
            return s * scale

    raise ValueError("未找到有效数值列")


def align_to_time(t: pd.Series, s: pd.Series) -> np.ndarray:
    return (
        s.reindex(pd.DatetimeIndex(t))
        .interpolate("time")
        .fillna(0.0)
        .to_numpy(dtype=float)
    )


# ============================================================
# 调度
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
# 主入口
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

    try:
        dbg("ENTER plan_energy_system df_load", df_load)
        t, load_kw, warn = normalize_time_and_load(df_load, time_col, load_col, units)
        dt = infer_dt_hours(t)
    except Exception as e:
        return {
            "feasible": False,
            "diagnosis": {"stage": "load", "msg": str(e)}
        }

    wind_kw = np.zeros_like(load_kw)
    if wind_input is not None:
        try:
            w = as_time_series(
                wind_input, time_col,
                ("WindPower_MW", "wind_mw", "wind_kw"),
                1000.0 if units.wind_power.lower() == "mw" else 1.0
            )
            wind_kw = align_to_time(t, w)
        except Exception as e:
            return {"feasible": False, "diagnosis": {"stage": "wind", "msg": str(e)}}

    pv_kw = np.zeros_like(load_kw)
    if pv_unit_kw is not None:
        try:
            p = as_time_series(
                pv_unit_kw, time_col,
                ("pv_unit_kw", "pv_kw", "value"),
                1.0
            )
            pv_kw = align_to_time(t, p)
        except Exception as e:
            return {"feasible": False, "diagnosis": {"stage": "pv", "msg": str(e)}}

    gen_kw = wind_kw + pv_kw

    if pv_unit_kw is None and wind_input is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_GENERATION",
                "msg": "无 PV / 无风，仅储能不能创造能量"
            }
        }

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
        "warnings": warn,
        "context": {
            "dt_hours": dt,
            "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python"
        }
    }




# 测试代码 main 函数
def main():
    # ------------------------------
    # 
    # ------------------------------
    Q1_load = pd.read_csv("D:\\228-售前测算\\宁德-山西项目测算输入\\Q1_load.csv", encoding="utf_8_sig")
    print(Q1_load.iloc[:, 0])
    
    df_total = pd.DataFrame()
    year = 2025
    start_index = 0
    for month in range(1,4):
        days_in_month = calendar.monthrange(year,month)[1]
        for day in range(1, days_in_month + 1):
            start_time = datetime(year, month, day, 0, 0, 0)
            end_time = datetime(year, month, day, 23, 45, 0)
            times = pd.date_range(start_time, end_time, freq="15min")
            # 创建DataFrame并添加timeStamp列
            temp_df = pd.DataFrame({"time": times, "value": 0.0})
            temp_df.loc[:, "value"] = Q1_load.iloc[:, month].to_list()
            temp_df["value"] = pd.to_numeric(temp_df["value"], errors='coerce')
            temp_df["value"] = temp_df["value"] * 1000
            df_total = pd.concat([df_total, temp_df],axis = 0)
    print(df_total)
    # ------------------------------
    # 
    # ------------------------------
    Q2_load = pd.read_csv("D:\\228-售前测算\\宁德-山西项目测算输入\\Q2_load.csv", encoding="utf_8_sig")
    
    year = 2025
    start_index = 0
    for month in range(1+3,4+3):
        days_in_month = calendar.monthrange(year,month)[1]
        for day in range(1, days_in_month + 1):
            start_time = datetime(year, month, day, 0, 0, 0)
            end_time = datetime(year, month, day, 23, 45, 0)
            times = pd.date_range(start_time, end_time, freq="15min")
            # 创建DataFrame并添加timeStamp列
            temp_df = pd.DataFrame({"time": times, "value": 0.0})
            temp_df.loc[:, "value"] = Q2_load.iloc[:, month - 3].to_list()
            temp_df["value"] = pd.to_numeric(temp_df["value"], errors='coerce')
            temp_df["value"] = temp_df["value"] * 1000
            df_total = pd.concat([df_total, temp_df],axis = 0)
    print(df_total)
    # ------------------------------
    # 
    # ------------------------------
    Q3_load = pd.read_csv("D:\\228-售前测算\\宁德-山西项目测算输入\\Q3_load.csv", encoding="utf_8_sig")
    year = 2025
    start_index = 0
    for month in range(1+6,4+6):
        days_in_month = calendar.monthrange(year,month)[1]
        for day in range(1, days_in_month + 1):
            start_time = datetime(year, month, day, 0, 0, 0)
            end_time = datetime(year, month, day, 23, 45, 0)
            times = pd.date_range(start_time, end_time, freq="15min")
            # 创建DataFrame并添加timeStamp列
            temp_df = pd.DataFrame({"time": times, "value": 0.0})
            temp_df.loc[:, "value"] = Q3_load.iloc[:, month - 6].to_list()
            temp_df["value"] = pd.to_numeric(temp_df["value"], errors='coerce')
            temp_df["value"] = temp_df["value"] * 1000
            df_total = pd.concat([df_total, temp_df],axis = 0)
    print(df_total)
    # ------------------------------
    # 
    # ------------------------------
    Q4_load = pd.read_csv("D:\\228-售前测算\\宁德-山西项目测算输入\\Q4_load.csv", encoding="utf_8_sig")
    year = 2025
    start_index = 0
    for month in range(1+9,4+9):
        days_in_month = calendar.monthrange(year,month)[1]
        for day in range(1, days_in_month + 1):
            start_time = datetime(year, month, day, 0, 0, 0)
            end_time = datetime(year, month, day, 23, 45, 0)
            times = pd.date_range(start_time, end_time, freq="15min")
            # 创建DataFrame并添加timeStamp列
            temp_df = pd.DataFrame({"time": times, "value": 0.0})
            temp_df.loc[:, "value"] = Q4_load.iloc[:, month - 9].to_list()
            temp_df["value"] = pd.to_numeric(temp_df["value"], errors='coerce')
            temp_df["value"] = temp_df["value"] * 1000
            df_total = pd.concat([df_total, temp_df],axis = 0)
    print(df_total)
    # ------------------------------
    # 
    # ------------------------------
    df_total.rename(columns={"time":"Time", "value":"P_kw"},inplace=True)
    df_total.to_csv("D:\\228-售前测算\\宁德-山西项目测算输入\\ningde_shanxi_load.csv", encoding="utf-8", index=None)
    res = plan_energy_system(
        df_load=df_total,
        time_col="Time",
        load_col="P_kw",
    )
    print(res)
    
if __name__ == "__main__":
    main()
