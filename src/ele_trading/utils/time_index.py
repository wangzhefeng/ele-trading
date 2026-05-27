from __future__ import annotations

import numpy as np
import pandas as pd


def infer_dt_hours(t) -> float:
    t = pd.Series(pd.to_datetime(t), name="Time").sort_values().reset_index(drop=True)
    if len(t) < 2:
        raise ValueError("时间点数量不足，无法推断 dt")
    dt = t.diff().dropna().mode().iloc[0]
    return float(dt.total_seconds() / 3600.0)


def monthly_kwh(time_index, kw_arr: np.ndarray, dt_hours: float) -> pd.Series:
    idx = pd.DatetimeIndex(pd.to_datetime(time_index).to_numpy())
    s = pd.Series(np.asarray(kw_arr, dtype="float64"), index=idx)
    return (s.groupby(s.index.to_period("M")).sum() * dt_hours).sort_index()
