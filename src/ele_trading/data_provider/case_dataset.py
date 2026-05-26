from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .time_series_ops import align_series_on_timestamp


@dataclass(slots=True)
class CaseDatasetConfig:
    mode: str
    freq: str
    include_load: bool
    include_pv: bool
    include_wind: bool
    include_prices: bool


@dataclass(slots=True)
class CaseDataset:
    frame: pd.DataFrame
    metadata: dict[str, str | float | int]


def build_investment_case_dataset(
    load_df: pd.DataFrame,
    pv_series: pd.Series | None = None,
    wind_series: pd.Series | None = None,
    price_df: pd.DataFrame | None = None,
) -> CaseDataset:
    frames = [load_df[["timestamp", "load_kw", "quality_score"]].copy()]
    if pv_series is not None:
        frames.append(pv_series.rename("pv_kw").to_frame().reset_index(names="timestamp"))
    if wind_series is not None:
        frames.append(wind_series.rename("wind_kw").to_frame().reset_index(names="timestamp"))
    if price_df is not None:
        frames.append(price_df[["timestamp", "buy_price", "sell_price"]].copy())

    frame = align_series_on_timestamp(frames)
    frame["pv_kw"] = frame.get("pv_kw", 0.0).fillna(0.0)
    frame["wind_kw"] = frame.get("wind_kw", 0.0).fillna(0.0)
    frame["net_load_kw"] = frame["load_kw"] - frame["pv_kw"] - frame["wind_kw"]
    frame["data_quality_flag"] = frame["quality_score"].fillna(1.0).apply(lambda value: "ok" if value >= 0.8 else "degraded")
    return CaseDataset(
        frame=frame,
        metadata={"mode": "investment_eval", "rows": len(frame)},
    )


def build_trading_case_dataset(
    load_df: pd.DataFrame,
    pv_series: pd.Series | None = None,
    wind_series: pd.Series | None = None,
    price_df: pd.DataFrame | None = None,
) -> CaseDataset:
    investment = build_investment_case_dataset(load_df, pv_series, wind_series, price_df).frame
    trading = pd.DataFrame(
        {
            "timestamp": investment["timestamp"],
            "load_forecast_kw": investment["load_kw"],
            "pv_forecast_kw": investment["pv_kw"],
            "wind_forecast_kw": investment["wind_kw"],
            "price_forecast": investment.get("buy_price", 0.0),
            "scenario_id": "base",
            "availability_flag": True,
            "quality_score": investment.get("quality_score", 1.0),
        }
    )
    return CaseDataset(
        frame=trading,
        metadata={"mode": "market_trading", "rows": len(trading)},
    )
