"""Market snapshot construction and CSV loading."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pandas as pd

from .contracts import MarketDataSnapshot
from .quality import align_series_on_timestamp
from .schemas import ObservedPowerSeries, PriceSeries, ScenarioRecord


def load_price_series(
    path: str | Path,
    time_col: str,
    price_col: str,
    label: str,
) -> PriceSeries:
    """Load a simple price series used by active trading samples."""
    frame = pd.read_csv(path)
    return PriceSeries(
        timestamps=frame[time_col].astype(int).tolist(),
        prices=frame[price_col].astype(float).tolist(),
        label=label,
    )


def load_price_scenarios(path: str | Path) -> List[ScenarioRecord]:
    """Load price scenario rows used by active trading samples."""
    frame = pd.read_csv(path)
    return [
        ScenarioRecord(**row)
        for row in frame.to_dict(orient="records")
    ]


def scenario_weights(records: Iterable[ScenarioRecord]) -> dict[str, float]:
    """Aggregate the configured weight for each scenario."""
    weights: dict[str, float] = {}
    for record in records:
        weights[record.scenario] = float(record.weight)
    return weights


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
    """Load timestamped CSV rows as a traceable market-data snapshot."""
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
    """Load observed load or renewable power without investment semantics."""
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
    """Build the active trading forecast snapshot directly from source frames."""
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

    aligned = align_series_on_timestamp(frames)
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
    for column in ("pv_forecast_kw", "wind_forecast_kw"):
        trading[column] = trading[column].fillna(0.0)

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
