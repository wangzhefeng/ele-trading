from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd


def align_to_time(t: pd.Series, s: pd.Series) -> np.ndarray:
    """将 Series s 对齐到时间轴 t，线性插值，缺失填 0。"""
    idx = pd.DatetimeIndex(pd.to_datetime(t))
    s = s.copy()
    s.index = pd.to_datetime(s.index)

    if len(s) == len(idx) and (s.index.values == idx.values).all():
        out = s.to_numpy(dtype="float64")
    else:
        out = s.reindex(idx).interpolate("time").fillna(0.0).to_numpy(dtype="float64")

    return np.ascontiguousarray(out, dtype=np.float64)


def as_time_series(
    x: pd.Series | pd.DataFrame,
    time_col: str,
    value_cols: Tuple[str, ...],
    scale: float,
) -> pd.Series:
    """将 Series 或 DataFrame 规范为 Series(index=DatetimeIndex)。"""
    if isinstance(x, pd.Series):
        s = pd.to_numeric(x, errors="coerce").fillna(0.0) * float(scale)
        s.index = pd.to_datetime(s.index)
        return s
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
    else:
        raise TypeError("输入必须是 pd.Series 或 pd.DataFrame")


def normalize_time_and_load(
    df: pd.DataFrame,
    time_col: str,
    load_col: str,
    load_unit: str = "kW",
) -> Tuple[pd.Series, np.ndarray, list]:
    """规范化负荷 DataFrame：提取时间轴和负荷数组（kW），按时间排序。"""
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df_load 类型错误：{type(df)}")

    if load_col not in df.columns:
        raise KeyError(f"负荷列 '{load_col}' 不存在")

    warn = []
    if time_col in df.columns:
        t = pd.Series(pd.to_datetime(df[time_col]), name="Time")
    elif isinstance(df.index, pd.DatetimeIndex):
        t = pd.Series(pd.to_datetime(df.index), name="Time")
        warn.append("使用 DatetimeIndex 作为时间轴")
    else:
        raise ValueError("未找到时间列，且 index 不是 DatetimeIndex")

    load = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype=float).copy()
    if load_unit.lower() == "mw":
        load *= 1000.0

    order = np.argsort(t.values)
    t = t.iloc[order].reset_index(drop=True)
    load = load[order]
    return t, load, warn


def align_and_merge(
    df_load: pd.DataFrame,
    df_wind: pd.DataFrame,
    load_col_kw: str = "P_kw",
    wind_col_mw: str = "WindPower_MW",
    freq: str | None = None,
) -> Tuple[pd.DataFrame, float]:
    """将负荷（kW）和风电（MW）对齐到统一时间轴。"""

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
