from __future__ import annotations

from typing import Any

import pandas as pd

from ele_trading.optimization.interfaces import (
    UserSidePVDispatchInput,
    UserSidePVExportParams,
)


def build_synthetic_user_side_pv_dispatch_frame(config: dict[str, Any]) -> pd.DataFrame:
    """Build deterministic load, PV, price, and price-type data for PV-only dispatch."""
    synthetic = config["synthetic_data"]
    start_time = pd.to_datetime(synthetic["start_time"])
    periods = int(synthetic["periods"])
    freq_minutes = int(synthetic["freq_minutes"])
    timestamps = pd.date_range(start=start_time, periods=periods, freq=f"{freq_minutes}min")

    records = []
    for timestamp in timestamps:
        price_type = _price_type_for_hour(timestamp.hour, synthetic["price_type_periods"])
        records.append(
            {
                "timestamp": timestamp,
                "load_forecast": _load_for_hour(timestamp.hour, synthetic),
                "pv_forecast": _pv_for_hour(timestamp.hour, synthetic),
                "buy_price": float(synthetic[f"{price_type}_price"]),
                "price_type": price_type,
            }
        )
    return pd.DataFrame.from_records(records)


def build_user_side_pv_dispatch_input(
    config: dict[str, Any],
) -> UserSidePVDispatchInput:
    """Build PV-only dispatch input from deterministic demo config."""
    frame = build_synthetic_user_side_pv_dispatch_frame(config)
    dispatch_config = config["dispatch"]
    export_config = config["export"]
    return UserSidePVDispatchInput(
        timestamps=frame["timestamp"].tolist(),
        load_forecast=frame["load_forecast"].astype(float).tolist(),
        pv_forecast=frame["pv_forecast"].astype(float).tolist(),
        buy_price=frame["buy_price"].astype(float).tolist(),
        price_type=frame["price_type"].astype(str).tolist(),
        export=UserSidePVExportParams(
            allow_export=bool(export_config["allow_export"]),
            sell_price=float(export_config["sell_price"]),
            export_limit=_optional_float(export_config.get("export_limit")),
            curtailment_cost_rate=float(export_config.get("curtailment_cost_rate", 0.0)),
        ),
        demand_charge_rate=float(dispatch_config["demand_charge_rate"]),
        step_hours=float(dispatch_config["step_hours"]),
    )


def _load_for_hour(hour: int, synthetic: dict[str, Any]) -> float:
    base_load = float(synthetic["base_load"])
    midday_peak = float(synthetic["midday_peak_load"])
    evening_peak = float(synthetic["evening_peak_load"])
    if 11 <= hour < 14:
        return base_load + midday_peak
    if 18 <= hour < 21:
        return base_load + evening_peak
    if 7 <= hour < 18:
        return base_load + 1.5
    return base_load


def _pv_for_hour(hour: int, synthetic: dict[str, Any]) -> float:
    peak_power = float(synthetic["pv_peak_power"])
    if hour < 6 or hour > 18:
        return 0.0
    distance_from_noon = abs(hour - 12)
    return peak_power * max(0.0, 1.0 - distance_from_noon / 6.0)


def _price_type_for_hour(hour: int, periods: list[dict[str, Any]]) -> str:
    for period in periods:
        if int(period["start_hour"]) <= hour < int(period["end_hour"]):
            return str(period["type"])
    raise ValueError(f"no price type configured for hour: {hour}")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
