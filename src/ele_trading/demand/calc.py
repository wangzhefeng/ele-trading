from __future__ import annotations

import pandas as pd

from ele_trading.utils.time_index import infer_dt_hours

from .config import DemandConfig, DemandResult


def calc_fixed_window(power: pd.Series, window_minutes: int) -> pd.Series:
    """固定不重叠窗口平均功率。

    每 *window_minutes* 分钟为一个完整周期，取该周期内功率均值。

    Parameters
    ----------
    power : Series
        功率时间序列（index=DatetimeIndex）。
    window_minutes : int
        窗口时长（分钟）。

    Returns
    -------
    Series
        窗口平均功率（index=窗口起始时间）。
    """
    return power.resample(f"{window_minutes}min").mean().dropna()


def calc_sliding_window(power: pd.Series, window_minutes: int) -> pd.Series:
    """滑动窗口平均功率。

    以 *window_minutes* 分钟为窗口宽度，逐点滑动取均值。

    Parameters
    ----------
    power : Series
        功率时间序列（index=DatetimeIndex）。
    window_minutes : int
        窗口时长（分钟）。

    Returns
    -------
    Series
        滑动平均功率序列（与原序列等长）。
    """
    dt_hours = infer_dt_hours(power.index)
    dt_minutes = dt_hours * 60
    window_steps = max(1, int(round(window_minutes / dt_minutes)))
    return power.rolling(window=window_steps, min_periods=1).mean()


def calc_demand(power: pd.Series, config: DemandConfig) -> DemandResult:
    """计算最大需量。

    根据配置的窗口类型和时长，计算窗口平均功率序列，
    并聚合出全局/月度/日度最大需量。

    Parameters
    ----------
    power : Series
        功率时间序列（index=DatetimeIndex, 单位与 config.power_unit 一致）。
    config : DemandConfig
        计算配置。

    Returns
    -------
    DemandResult
    """
    # 单位换算: MW -> kW
    if config.power_unit == "MW":
        power = power * 1000.0

    if config.window_type == "fixed":
        ws = calc_fixed_window(power, config.window_minutes)
    else:
        ws = calc_sliding_window(power, config.window_minutes)

    peak_idx = ws.idxmax()
    max_demand = float(ws.loc[peak_idx])

    monthly_max = ws.groupby(ws.index.to_period("M")).max()
    daily_max = ws.groupby(ws.index.date).max()
    daily_max.index = pd.to_datetime(daily_max.index)

    return DemandResult(
        max_demand=max_demand,
        peak_timestamp=pd.Timestamp(peak_idx),
        monthly_max=monthly_max,
        daily_max=daily_max,
        window_series=ws,
        config=config,
    )


def calc_demand_charge(result: DemandResult, demand_price: float | None = None) -> dict[str, float]:
    """计算需量电费。

    Parameters
    ----------
    result : DemandResult
        需量计算结果。
    demand_price : float, optional
        需量电价（元/kW/月），为 None 时使用 result.config.demand_price。

    Returns
    -------
    dict
        含 max_demand_kw, demand_price, demand_charge 三项。
    """
    price = demand_price if demand_price is not None else result.config.demand_price
    return {
        "max_demand_kw": result.max_demand,
        "demand_price": price,
        "demand_charge": result.max_demand * price,
    }
