from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from ele_trading.optimization.interfaces import (
    CvxpBESSDispatchInput,
    CvxpBESSProfile,
    UserSideBESSParams,
)
from ele_trading.optimization.cvxp_bess_dispatch import get_cvxp_profile


def load_cvxp_bess_dispatch_config(path: str | Path) -> dict[str, Any]:
    """加载 CVXPY 储能调度 demo 配置。"""
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("cvxp bess dispatch config must be a mapping")
    return config


def build_synthetic_cvxp_dispatch_frame(config: dict[str, Any]) -> pd.DataFrame:
    """构建含 demand_load、ele_prices、ele_types 的合成数据。"""
    synthetic = config["synthetic_data"]
    start_time = pd.to_datetime(synthetic["start_time"])
    periods = int(synthetic["periods"])
    freq_minutes = int(synthetic["freq_minutes"])
    timestamps = pd.date_range(start=start_time, periods=periods, freq=f"{freq_minutes}min")

    records = []
    for timestamp in timestamps:
        ele_type = _ele_type_for_hour(timestamp.hour, synthetic["price_type_periods"])
        price_key = _price_key_for_type(ele_type)
        records.append(
            {
                "timestamp": timestamp,
                "demand_load": _load_for_hour(timestamp.hour, synthetic),
                "ele_prices": float(synthetic[price_key]),
                "ele_types": ele_type,
            }
        )
    return pd.DataFrame.from_records(records)


def build_cvxp_bess_dispatch_input(
    config: dict[str, Any],
) -> CvxpBESSDispatchInput:
    """构建 CVXPY 调度算法输入。"""
    frame = build_synthetic_cvxp_dispatch_frame(config)
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

    version = dispatch_config.get("version", "optim")
    profile = get_cvxp_profile(version)

    return CvxpBESSDispatchInput(
        timestamps=frame["timestamp"].tolist(),
        demand_load=frame["demand_load"].astype(float).tolist(),
        ele_prices=frame["ele_prices"].astype(float).tolist(),
        ele_types=frame["ele_types"].astype(str).tolist(),
        bess=bess,
        initial_soc=float(bess_config.get("initial_soc", 0.0)),
        max_demand_price=float(dispatch_config.get("max_demand_price", 0.0)),
        freq_minutes=int(dispatch_config.get("freq_minutes", 60)),
        profile=profile,
        transform_capacity=float(dispatch_config.get("transform_capacity", 0.0)),
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
        return base_load + 1500.0
    return base_load


def _ele_type_for_hour(hour: int, periods: list[dict[str, Any]]) -> str:
    for period in periods:
        if int(period["start_hour"]) <= hour < int(period["end_hour"]):
            return str(period["type"])
    raise ValueError(f"no price type configured for hour: {hour}")


_PRICE_KEY_MAP = {
    "深谷": "deep_valley_price",
    "谷": "valley_price",
    "平": "flat_price",
    "峰": "peak_price",
    "尖峰": "sharp_peak_price",
}


def _price_key_for_type(ele_type: str) -> str:
    if ele_type in _PRICE_KEY_MAP:
        return _PRICE_KEY_MAP[ele_type]
    raise ValueError(f"unknown electricity type: {ele_type}")
