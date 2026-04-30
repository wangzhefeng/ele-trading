# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim_common.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042027
# * Description : storage_optim_*.py 公共函数
# ***************************************************

import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd

# ============================================================
# Numba（统一入口，所有 storage_optim_*.py 从此导入）
# ============================================================
try:
    from numba import njit
    NUMBA_OK = True
except Exception:
    NUMBA_OK = False
    def njit(*args, **kwargs):
        def deco(f): return f
        return deco

# ============================================================
# 配置数据类
# ============================================================
@dataclass
class BESSConfig:
    """
    储能物理参数
    """
    freq: str = "1h"
    eta_charge: float = 0.92
    eta_discharge: float = 0.92
    soc_init: float = 0.50
    soc_min: float = 0.10
    soc_max: float = 1.00
    c_rate: float = 1.0
    hours_to_full: float = 2.0        # 0.5C => 2h 充满
    switch_gap_hours: float = 1.0     # 充放之间间隔 1 小时
    enforce_terminal_soc: bool = True


@dataclass
class Targets:
    """
    优化约束目标（新能源消纳率 / 负荷覆盖率）
    """
    min_green_self_consumption: float = 0.60  # used / gen
    min_load_coverage: float = 0.30           # used / load


@dataclass
class Investment:
    """
    # TODO 补充注释
    """
    capex_cny_per_kwh: float = 1000.0


@dataclass
class ShiftPolicy:
    """
    允许在 Wind < Load 时也能充电平移（通过牺牲当期供电来换未来供电）。
        - enable_shift: 是否开启
        - lookahead_steps: 未来窗口长度（用于判断是否“值得”储能平移）
        - shift_max_frac_of_wind: 当 Wind < Load 时，最多拿走 Wind 的多少比例去充电
    说明：
      这会减少当期 served，但可能提升未来某些时段的 served（更符合“平移”直觉）。
      若你只关心总覆盖率/自用率，理论上它不一定更优，但你明确要求支持该行为。
    """
    enable_shift: bool = True
    lookahead_steps: int = 8
    shift_max_frac_of_wind: float = 0.30


@dataclass
class UnitsConfig:
    """
    输入数据单位声明
    """
    load_power: str = "kW"   # kW / MW
    pv_power: str = "kW"     # kW/kWp
    wind_power: str = "MW"   # MW / kW


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
    # ---- 约束比例（默认值）----
    self_use_ratio_min: float = 0.60    # PV_used / PV_gen
    load_cover_ratio_min: float = 0.20  # PV_used / Load
    # ---- 约束口径 ----
    constraint_mode: str = "annual"     # "annual" or "monthly"
    monthly_all_must_meet: bool = True
    # ---------- PV 搜索 ----------
    pv_step_coarse_kwp: float = 2000.0
    pv_step_fine_kwp: float = 250.0
    pv_refine_window_kwp: float = 8000.0
    pv_min_kwp: float = 0.0
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
# 时间工具
# ============================================================
def infer_dt_hours(t) -> float:
    """
    推断时间序列步长（小时）。
    接受 pd.Series / pd.DatetimeIndex / list。
    """
    t = pd.Series(pd.to_datetime(t), name="Time").sort_values().reset_index(drop=True)
    if len(t) < 2:
        raise ValueError("时间点数量不足，无法推断 dt")
    dt = t.diff().dropna().mode().iloc[0]
    
    return float(dt.total_seconds() / 3600.0)

# ============================================================
# 数据对齐与聚合
# ============================================================
def align_to_time(t: pd.Series, s: pd.Series) -> np.ndarray:
    """
    将 Series s 对齐到时间轴 t，线性插值，缺失填 0。
    返回 contiguous float64 ndarray。
    """
    idx = pd.DatetimeIndex(pd.to_datetime(t))
    s = s.copy()
    s.index = pd.to_datetime(s.index)

    if len(s) == len(idx) and (s.index.values == idx.values).all():
        out = s.to_numpy(dtype="float64")
    else:
        out = s.reindex(idx).interpolate("time").fillna(0.0).to_numpy(dtype="float64")

    return np.ascontiguousarray(out, dtype=np.float64)


def monthly_kwh(df_time: pd.Series, kw_arr: np.ndarray, dt_hours: float) -> pd.Series:
    """
    按月累计电量（kWh）
    """
    idx = pd.DatetimeIndex(pd.to_datetime(df_time).to_numpy())
    s = pd.Series(kw_arr, index=idx)

    return (s.groupby(s.index.to_period("M")).sum() * dt_hours).sort_index()


def as_time_series(
    x: Union[pd.Series, pd.DataFrame],
    time_col: str,
    value_cols: Tuple[str, ...],
    scale: float,
) -> pd.Series:
    """
    将 Series 或 DataFrame 规范为 Series(index=DatetimeIndex)。
    value_cols 按顺序尝试匹配列名；scale 用于单位换算（如 MW→kW 传 1000.0）。
    """
    if isinstance(x, pd.Series):
        s = pd.to_numeric(x, errors="coerce").fillna(0.0)
        s.index = pd.to_datetime(s.index)
        return s * scale
    elif isinstance(x, pd.DataFrame):
        df = x.copy()
        if time_col in df.columns:
            t = pd.to_datetime(df[time_col])
            df = df.drop(columns=[time_col])
        else:
            t = pd.to_datetime(df.index)
            df = df.reset_index(drop=True)

        for c in value_cols:
            if c in df.columns:
                s = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
                s.index = t
                return s * scale
        raise ValueError(f"未找到有效数值列，尝试过：{value_cols}")
    elif not isinstance(x, pd.DataFrame):
        raise TypeError("输入必须是 pd.Series 或 pd.DataFrame")


def normalize_time_and_load(
    df: pd.DataFrame,
    time_col: str,
    load_col: str,
    units: UnitsConfig,
) -> Tuple[pd.Series, np.ndarray, list]:
    """
    规范化负荷 DataFrame：提取时间轴（Series）和负荷数组（ndarray, kW），按时间排序。
    返回 (t, load_kw, warnings_list)。
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df_load 类型错误：{type(df)}")
    
    if load_col not in df.columns:
        raise KeyError(f"负荷列 '{load_col}' 不存在")

    # 时间列预处理为一个 pd.Series
    warn = []
    if time_col in df.columns:
        t = pd.Series(pd.to_datetime(df[time_col]), name="Time")
    elif isinstance(df.index, pd.DatetimeIndex):
        t = pd.Series(pd.to_datetime(df.index), name="Time")
        warn.append("使用 DatetimeIndex 作为时间轴")
    else:
        raise ValueError("未找到时间列，且 index 不是 DatetimeIndex")
    
    # 负荷数值转换
    load = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    # 负荷单位转换
    if units.load_power.lower() == "mw":
        load *= 1000.0

    # 按照时间顺序排序
    order = np.argsort(t.values)
    t = t.iloc[order].reset_index(drop=True)
    load = load[order]
    return t, load, warn

# ============================================================
# I/O
# ============================================================
def read_timeseries(obj: Union[str, pd.DataFrame]) -> pd.DataFrame:
    """
    读取 CSV / Excel / DataFrame，统一返回 DatetimeIndex 的 DataFrame。
    要求有 Time 列或 DatetimeIndex。
    """
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
    elif isinstance(obj, str):
        ext = os.path.splitext(obj.lower())[1]
        if ext == ".csv":
            df = pd.read_csv(obj)
        elif ext in [".xls", ".xlsx"]:
            df = pd.read_excel(obj)
        else:
            raise ValueError(f"不支持的文件格式：{ext}")
    else:
        raise TypeError("输入必须是 DataFrame 或文件路径（str）")

    if "Time" in df.columns:
        df["Time"] = pd.to_datetime(df["Time"])
        df = df.set_index("Time")
    elif isinstance(df.index, pd.DatetimeIndex):
        pass
    else:
        raise ValueError("输入必须包含 Time 列或 DatetimeIndex")

    return df.sort_index()


def align_and_merge(
    df_load: pd.DataFrame,
    df_wind: pd.DataFrame,
    load_col_kw: str = "P_kw",
    wind_col_mw: str = "WindPower_MW",
    freq: Optional[str] = None,
) -> Tuple[pd.DataFrame, float]:
    """
    将负荷（kW）和风电（MW）对齐到统一时间轴。
    返回 (df, dt_h)；df.columns = [Load_kW, Wind_kW]，index = DatetimeIndex。
    """
    def _to_index(df_in: pd.DataFrame, label: str) -> pd.DataFrame:
        df_out = df_in.copy()
        if "Time" in df_out.columns:
            df_out["Time"] = pd.to_datetime(df_out["Time"])
            df_out = df_out.set_index("Time")
        elif isinstance(df_out.index, pd.DatetimeIndex):
            pass
        else:
            raise ValueError(f"{label} 必须有 Time 列或 DatetimeIndex")
        return df_out

    def _infer_mins(idx: pd.DatetimeIndex) -> int:
        if len(idx) < 2:
            return 15
        d = np.diff(idx.view("i8"))
        d = d[d > 0]
        return max(1, int(round(np.median(d) / 1e9 / 60))) if len(d) else 15

    dfl = _to_index(df_load, "df_load")
    dfw = _to_index(df_wind, "df_wind")

    if load_col_kw not in dfl.columns:
        raise ValueError(f"负荷列 '{load_col_kw}' 不存在")
    if wind_col_mw not in dfw.columns:
        raise ValueError(f"风电列 '{wind_col_mw}' 不存在")

    dfl = dfl[[load_col_kw]].rename(columns={load_col_kw: "Load_kW"})
    dfw = dfw[[wind_col_mw]].copy()
    dfw["Wind_kW"] = dfw[wind_col_mw].astype(float) * 1000.0
    dfw = dfw[["Wind_kW"]]

    if freq is None:
        mins = min(_infer_mins(dfl.index), _infer_mins(dfw.index))
        freq = f"{mins}min"

    idx = pd.date_range(
        start=max(dfl.index.min(), dfw.index.min()),
        end=min(dfl.index.max(), dfw.index.max()),
        freq=freq,
    )
    dfl = dfl.reindex(idx).interpolate("time").ffill().bfill()
    dfw = dfw.reindex(idx).interpolate("time").ffill().bfill()

    df = pd.concat([dfl, dfw], axis=1)
    df.index.name = "Time"
    dt_h = pd.to_timedelta(freq).total_seconds() / 3600.0
    return df, float(dt_h)

# ============================================================
# 年度调度仿真
# ============================================================
@njit
def dispatch_numba(load_kw, 
                   gen_kw, 
                   dt, 
                   batt_kwh, 
                   eta, 
                   c_rate, 
                   soc0, 
                   soc_min_f, 
                   soc_max_f):
    """
    贪心调度（Numba JIT）。
    gen_kw 为合并后新能源出力（kW）；充放均遵从 C-rate 和 SOC 约束；效率 √η 分担。
    返回：(gen_kwh, used_kwh, load_kwh, bess_discharge_kwh)
    """
    gen_e = used_e = load_e = bess_dis = 0.0

    if batt_kwh <= 0:
        for i in range(load_kw.shape[0]):
            L = max(load_kw[i], 0.0)
            G = max(gen_kw[i], 0.0)
            load_e += L * dt
            gen_e += G * dt
            used_e += (L if L < G else G) * dt
        return gen_e, used_e, load_e, bess_dis

    soc = soc0 * batt_kwh
    soc_min = soc_min_f * batt_kwh
    soc_max = soc_max_f * batt_kwh
    pmax = c_rate * batt_kwh
    eta_c = eta ** 0.5
    eta_d = eta ** 0.5

    for i in range(load_kw.shape[0]):
        L = max(load_kw[i], 0.0)
        G = max(gen_kw[i], 0.0)
        
        load_e += L * dt
        gen_e += G * dt

        direct = L if L < G else G
        used_e += direct * dt
        
        surplus = G - direct
        deficit = L - direct

        if surplus > 1e-9 and soc < soc_max:
            ch = surplus
            if ch > pmax: 
                ch = pmax
            max_ch = (soc_max - soc) / dt
            if ch > max_ch: 
                ch = max_ch
            soc += ch * dt * eta_c

        if deficit > 1e-9 and soc > soc_min:
            dis = deficit
            if dis > pmax: 
                dis = pmax
            max_dis = (soc - soc_min) * eta_d / dt
            if dis > max_dis: 
                dis = max_dis
            soc -= dis * dt / eta_d
            used_e += dis * dt
            bess_dis += dis * dt

    return gen_e, used_e, load_e, bess_dis


def dispatch(load_kw: np.ndarray,
             gen_kw: np.ndarray,
             dt: float,
             batt_kwh: float,
             eta_roundtrip: float,
             c_rate: float,
             soc_init_frac: float,
             soc_min_frac: float,
             soc_max_frac: float,
             use_numba: bool = True) -> Dict[str, float]:
    """
    dispatch_numba 的 Python 封装，含 Numba / fallback 切换。
    """
    if use_numba and NUMBA_OK:
        g, u, l, b = dispatch_numba(
            load_kw, 
            gen_kw, 
            dt, 
            float(batt_kwh),
            float(eta_roundtrip), 
            float(c_rate),
            float(soc_init_frac), 
            float(soc_min_frac), 
            float(soc_max_frac),
        )
    else:
        g = float(np.maximum(gen_kw, 0.0).sum() * dt)
        l = float(np.maximum(load_kw, 0.0).sum() * dt)
        u = float(np.minimum(np.maximum(load_kw, 0.0), np.maximum(gen_kw, 0.0)).sum() * dt)
        b = 0.0

    return {
        "gen_kwh": float(g),
        "used_kwh": float(u),
        "load_kwh": float(l),
        "self_use_ratio": float(u / g) if g > 1e-9 else 0.0,
        "load_cover_ratio": float(u / l) if l > 1e-9 else 0.0,
        "bess_discharge_kwh": float(b),
    }


def evaluate(load_kw: np.ndarray, gen_kw: np.ndarray, dt: float, batt_kwh: float, cfg: Any) -> Dict[str, float]:
    """
    dispatch 的 config 对象封装。
    cfg 需要有：use_numba, eta_roundtrip, c_rate, soc_init_frac, soc_min_frac, soc_max_frac。
    """
    return dispatch(
        load_kw, gen_kw, dt, batt_kwh,
        cfg.eta_roundtrip, cfg.c_rate,
        cfg.soc_init_frac, cfg.soc_min_frac, cfg.soc_max_frac,
        use_numba=cfg.use_numba,
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




def main():
    pass

if __name__ == "__main__":
    main()
