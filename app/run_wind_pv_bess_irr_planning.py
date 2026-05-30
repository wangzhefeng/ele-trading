"""IRR 目标型 Wind+PV+BESS 容量规划运行脚本。"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np
import pandas as pd

from ele_trading.capacity_planning import (
    WindPVBESSIRRPlanConfig,
    plan_wind_pv_bess_for_target_irr,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger

CONFIG_PATH = PROJECT_ROOT / 'configs' / 'wind_pv_bess_irr_planning.yaml'


def _make_load(n_hours: int, timezone: str, load_mean_kw: float) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n_hours, freq="h", tz=timezone)
    hours = np.arange(n_hours) % 24
    day = np.arange(n_hours) / 24.0
    daily = 0.88 + 0.12 * ((hours >= 8) & (hours <= 22)).astype(float)
    seasonal = 1.0 + 0.06 * np.sin(2 * np.pi * day / 365.0)
    return pd.DataFrame({"Time": idx, "P_kw": load_mean_kw * daily * seasonal})


def _make_wind_unit(idx: pd.DatetimeIndex) -> pd.Series:
    day = np.arange(len(idx)) / 24.0
    hour = np.arange(len(idx)) % 24
    seasonal = 0.38 + 0.08 * np.cos(2 * np.pi * day / 365.0)
    intraday = 0.03 * np.sin(2 * np.pi * (hour - 2) / 24.0)
    return pd.Series(np.clip((seasonal + intraday) * 1000.0, 0.0, 1000.0), index=idx, name="wind_unit_kw")


def _make_pv_unit(idx: pd.DatetimeIndex) -> pd.Series:
    hour = np.arange(len(idx)) % 24
    day = np.arange(len(idx)) / 24.0
    daylight = np.maximum(0.0, np.sin(np.pi * (hour - 6) / 12.0))
    seasonal = 0.75 + 0.20 * np.cos(2 * np.pi * (day - 170.0) / 365.0)
    return pd.Series(np.clip(daylight * seasonal, 0.0, 1.0), index=idx, name="pv_unit_kw")


def _to_config(config: dict) -> WindPVBESSIRRPlanConfig:
    price = config["price"]
    constraints = config["constraints"]
    capacity = config["capacity"]
    search = config["search"]
    bess = config["bess"]
    cost = config["cost"]
    return WindPVBESSIRRPlanConfig(
        target_owner_price_yuan_per_kwh=price["target_owner_price_yuan_per_kwh"],
        grid_buy_price_yuan_per_kwh=price["grid_buy_price_yuan_per_kwh"],
        green_price_adder_yuan_per_kwh=price["green_price_adder_yuan_per_kwh"],
        target_irr=price["target_irr"],
        irr_tolerance=price["irr_tolerance"],
        self_use_ratio_min=constraints["self_use_ratio_min"],
        load_cover_ratio_min=constraints["load_cover_ratio_min"],
        wind_max_mw=capacity["wind_max_mw"],
        pv_max_mw=capacity["pv_max_mw"],
        bess_max_mwh=capacity["bess_max_mwh"],
        wind_step_mw=search["wind_step_mw"],
        pv_step_mw=search["pv_step_mw"],
        bess_step_mwh=search["bess_step_mwh"],
        eta_roundtrip=bess["eta_roundtrip"],
        c_rate=bess["c_rate"],
        soc_init_frac=bess["soc_init_frac"],
        soc_min_frac=bess["soc_min_frac"],
        soc_max_frac=bess["soc_max_frac"],
        switch_gap_hours=bess.get("switch_gap_hours", 0.0),
        wind_capex_yuan_per_kw=cost["wind_capex_yuan_per_kw"],
        pv_capex_yuan_per_kwp=cost["pv_capex_yuan_per_kwp"],
        bess_capex_yuan_per_kwh=cost["bess_capex_yuan_per_kwh"],
        annual_opex_ratio=cost["annual_opex_ratio"],
        life_years=cost["life_years"],
    )


def main() -> None:
    config = read_yaml(CONFIG_PATH)
    scenario = config["scenario"]
    df_load = _make_load(
        int(scenario["n_hours"]),
        scenario["timezone"],
        float(scenario["load_mean_kw"]),
    )
    idx = pd.DatetimeIndex(df_load["Time"])
    wind_unit = _make_wind_unit(idx)
    pv_unit = _make_pv_unit(idx)

    cfg = _to_config(config)
    result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)

    logger.info("=== IRR 目标型 Wind+PV+BESS 容量规划 ===")
    logger.info("status=%s", result.status)
    if result.status == "ok":
        logger.info(
            "wind_mw=%.2f pv_mw=%.2f bess_mwh=%.2f irr=%.4f ppa=%.4f green_price=%.4f owner_avg=%.4f",
            result.wind_mw,
            result.pv_mw,
            result.bess_mwh,
            result.irr or 0.0,
            result.ppa_price,
            result.green_price,
            result.owner_avg_price,
        )
        logger.info(
            "self_use=%.4f load_cover=%.4f total_capex=%.2f annual_cf=%.2f",
            result.self_use_ratio,
            result.load_cover_ratio,
            result.total_capex_yuan,
            result.annual_cashflow_yuan,
        )
    else:
        logger.info("message=%s", result.message)
        if result.diagnostics is not None and not result.diagnostics.empty:
            logger.info("nearest_candidate=%s", result.diagnostics.head(1).to_dict("records")[0])


if __name__ == "__main__":
    main()
