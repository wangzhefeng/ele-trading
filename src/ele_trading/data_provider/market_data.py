"""市场快照构造与 CSV 加载（活动实现层）。

向上提供两类能力：
1. 轻量读取：``load_price_series`` / ``load_observed_power_series``；
2. 快照构造：``load_market_data_csv``（CSV → MarketDataSnapshot）与
   ``build_trading_case_dataset``（源数据帧直接构造交易预测快照）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .contracts import MarketDataSnapshot
from .quality import align_series_on_timestamp
from .schemas import ObservedPowerSeries, PriceSeries


def load_price_series(
    path: str | Path,
    time_col: str,
    price_col: str,
    label: str,
) -> PriceSeries:
    """加载活动交易样例用的简单价格序列（整数时刻索引）。"""
    frame = pd.read_csv(path)
    return PriceSeries(
        timestamps=frame[time_col].astype(int).tolist(),
        prices=frame[price_col].astype(float).tolist(),
        label=label,
    )


def load_market_data_csv(
    path: str | Path,
    *,
    market: str,
    scope_type: str,
    scope_id: str,
    as_of: pd.Timestamp,
    version: str,
    quality_flags: tuple[str, ...] = (),
) -> MarketDataSnapshot:
    """把带时间戳的 CSV 加载为可溯源的市场数据快照。

    CSV 必须含 ``timestamp`` / ``is_observation`` 列；时区、单调性、
    防前瞻等约束由 ``MarketDataSnapshot`` 构造时强制校验。
    """
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return MarketDataSnapshot(
        market=market,
        scope_type=scope_type,
        scope_id=scope_id,
        as_of=as_of,
        frame=frame,
        version=version,
        quality_flags=quality_flags,
    )


def load_observed_power_series(
    path: str | Path,
    *,
    value_col: str,
    unit: str,
    time_col: str = "timestamp",
    quality_flags: tuple[str, ...] = (),
) -> ObservedPowerSeries:
    """加载实测负荷/新能源功率序列（无投资语义）。

    以 ``time_col`` 为 DatetimeIndex、``value_col`` 为值构造 Series；
    时区/单调/唯一/有限值约束由 ``ObservedPowerSeries`` 构造时校验。
    """
    frame = pd.read_csv(path)
    timestamps = pd.DatetimeIndex(pd.to_datetime(frame[time_col]))
    values = pd.Series(
        frame[value_col].astype(float).to_numpy(),
        index=timestamps,
        name=value_col,
    )
    return ObservedPowerSeries(
        values=values,
        unit=unit,
        source=str(path),
        quality_flags=quality_flags,
    )


def build_trading_case_dataset(
    load_df: pd.DataFrame,
    pv_series: pd.Series | None = None,
    wind_series: pd.Series | None = None,
    price_df: pd.DataFrame | None = None,
    *,
    market: str,
    scope_type: str,
    scope_id: str,
    as_of: pd.Timestamp,
    version: str,
) -> MarketDataSnapshot:
    """从源数据帧直接构造活动交易预测快照。

    口径说明：``price_forecast`` 只取 ``buy_price``，``sell_price`` 当前被静默丢弃；
    ``scenario_id`` 恒为 ``"base"``（确定性单轨迹）。接入真实数据时如需买卖双侧
    价格或多场景轨迹，需扩展输出列并同步下游消费方。

    输入约定：``load_df`` 含 ``timestamp/load_kw/quality_score`` 列；
    ``pv_series`` / ``wind_series`` 为 DatetimeIndex 功率序列；
    ``price_df`` 含 ``timestamp/buy_price/sell_price`` 列。
    """
    # --- 各路输入规整为 (timestamp, 值列) 帧，负荷为必需，其余可选 ---
    frames = [load_df[["timestamp", "load_kw", "quality_score"]].copy()]
    if pv_series is not None:
        frames.append(
            pv_series.rename("pv_kw").to_frame().reset_index(names="timestamp")
        )
    if wind_series is not None:
        frames.append(
            wind_series.rename("wind_kw").to_frame().reset_index(names="timestamp")
        )
    if price_df is not None:
        frames.append(
            price_df[["timestamp", "buy_price", "sell_price"]].copy()
        )

    # --- 按 timestamp 外连接对齐（缺列由下游默认值兜底） ---
    aligned = align_series_on_timestamp(frames)

    # --- 组装交易预测帧：全部行标记为预测（is_observation=False） ---
    trading = pd.DataFrame(
        {
            "timestamp": aligned["timestamp"],
            "load_forecast_kw": aligned["load_kw"],
            "pv_forecast_kw": aligned.get("pv_kw", 0.0),
            "wind_forecast_kw": aligned.get("wind_kw", 0.0),
            "price_forecast": aligned.get("buy_price", 0.0),
            "scenario_id": "base",
            "availability_flag": True,
            "quality_score": aligned.get("quality_score", 1.0),
            "is_observation": False,
        }
    )
    # 缺新能源输入时对齐产生的 NaN 按 0 出力处理
    for column in ("pv_forecast_kw", "wind_forecast_kw"):
        trading[column] = trading[column].fillna(0.0)

    # --- 质量标记：任一行质量分低于 0.8 即整体标 degraded ---
    quality_flags = (
        ("degraded",)
        if (trading["quality_score"].fillna(0.0) < 0.8).any()
        else ()
    )
    return MarketDataSnapshot(
        market=market,
        scope_type=scope_type,
        scope_id=scope_id,
        as_of=as_of,
        frame=trading,
        version=version,
        quality_flags=quality_flags,
    )
