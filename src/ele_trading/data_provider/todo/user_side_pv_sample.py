from __future__ import annotations

from typing import Any

import pandas as pd

from ele_trading.optimization.todo.interfaces import (
    UserSidePVDispatchInput,
    UserSidePVExportParams,
    UserSidePVBESSDispatchInput,
    UserSideBESSParams,
)


def build_synthetic_user_side_pv_dispatch_frame(config: dict[str, Any]) -> pd.DataFrame:
    """Build deterministic load, PV, price, and price-type data."""
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
    return UserSidePVDispatchInput(
        timestamps=frame["timestamp"].tolist(),
        load_forecast=frame["load_forecast"].astype(float).tolist(),
        pv_forecast=frame["pv_forecast"].astype(float).tolist(),
        buy_price=frame["buy_price"].astype(float).tolist(),
        price_type=frame["price_type"].astype(str).tolist(),
        export=_build_export_params(config),
        demand_charge_rate=float(dispatch_config["demand_charge_rate"]),
        step_hours=float(dispatch_config["step_hours"]),
    )


def build_user_side_pv_bess_dispatch_input(
    config: dict[str, Any],
) -> UserSidePVBESSDispatchInput:
    """Build PV + bess dispatch input from deterministic demo config."""
    pv_input = build_user_side_pv_dispatch_input(config)
    bess_config = config["bess"]
    dispatch_config = config["dispatch"]
    bess = UserSideBESSParams(
        capacity=float(bess_config["capacity"]),
        soc_min=float(bess_config["soc_min"]),
        soc_max=float(bess_config["soc_max"]),
        p_ch_max=float(bess_config["p_ch_max"]),
        p_dis_max=float(bess_config["p_dis_max"]),
        eta_ch=float(bess_config["eta_ch"]),
        eta_dis=float(bess_config["eta_dis"]),
    )
    return UserSidePVBESSDispatchInput(
        timestamps=pv_input.timestamps,
        load_forecast=pv_input.load_forecast,
        pv_forecast=pv_input.pv_forecast,
        buy_price=pv_input.buy_price,
        price_type=pv_input.price_type,
        export=pv_input.export,
        demand_charge_rate=pv_input.demand_charge_rate,
        step_hours=pv_input.step_hours,
        bess=bess,
        initial_soc=float(bess_config["initial_soc"]),
        terminal_soc_target=_optional_float(dispatch_config.get("terminal_soc_target")),
        cycle_cost_rate=float(dispatch_config.get("cycle_cost_rate", 0.0)),
    )


def _build_export_params(config: dict[str, Any]) -> UserSidePVExportParams:
    export_config = config.get("export", {})
    return UserSidePVExportParams(
        allow_export=bool(export_config.get("allow_export", True)),
        sell_price=float(export_config.get("sell_price", 0.0)),
        export_limit=_optional_float(export_config.get("export_limit")),
        curtailment_cost_rate=float(export_config.get("curtailment_cost_rate", 0.0)),
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
    # Deterministic triangular daytime curve: 0 at 6/18, peak at noon.
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
