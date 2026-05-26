from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pandas as pd
import yaml

from ele_trading.utils.log_util import logger
from wind_pv_es_calc.eva_PV_optim_version.prepare_legacy_temp_data import (
    build_legacy_total_frame,
    ensure_legacy_temp_data,
    load_bridge_config,
)


CONFIG_PATH = PROJECT_ROOT / "configs" / "wind_pv_legacy_profit_eval.yaml"


def load_config(path: str | Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("profit eval config must be a mapping")
    return config


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _read_time_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Time"] = pd.to_datetime(df["Time"])
    return df


def _infer_step_hours(time_series: pd.Series) -> float:
    if len(time_series) < 2:
        return 1.0
    return float((time_series.iloc[1] - time_series.iloc[0]).total_seconds() / 3600.0)


def _capital_recovery_factor(discount_rate: float, asset_life_years: int) -> float:
    if asset_life_years <= 0:
        raise ValueError("asset_life_years must be positive")
    if discount_rate == 0:
        return 1.0 / asset_life_years
    factor = (1 + discount_rate) ** asset_life_years
    return discount_rate * factor / (factor - 1)


def _build_buy_price_series(frame: pd.DataFrame, market_cfg: dict) -> pd.Series:
    source = market_cfg["buy_price_source"]
    mode = source["mode"]
    if mode == "flat":
        return pd.Series([float(source["flat_price"])] * len(frame), index=frame.index, name="buy_price")
    if mode == "csv":
        price_df = pd.read_csv(_resolve_path(source["csv_path"]))
        price_df[source["time_col"]] = pd.to_datetime(price_df[source["time_col"]])
        price_series = price_df.set_index(source["time_col"])[source["price_col"]].astype(float)
        return price_series.reindex(frame["Time"]).interpolate(method="time").ffill().bfill().reset_index(drop=True)
    raise ValueError(f"unsupported buy_price_source mode: {mode}")


def run_profit_eval(config: dict) -> dict[str, float]:
    bridge_config_path = _resolve_path(config["data"]["bridge_config_path"])
    if bool(config["data"].get("refresh_before_run", False)):
        ensure_legacy_temp_data(bridge_config_path)

    bridge_config = load_bridge_config(bridge_config_path)
    compatibility = bridge_config["compatibility"]

    load_df = _read_time_csv(_resolve_path(config["data"]["df_2025_path"]))
    pv_df = _read_time_csv(_resolve_path(config["data"]["df_pv_2025_path"]))
    wind_df = _read_time_csv(_resolve_path(config["data"]["df_wind_2025_path"]))
    total = build_legacy_total_frame(
        load_df=load_df,
        pv_df=pv_df,
        wind_df=wind_df,
        compatibility=compatibility,
        freq=str(bridge_config["run"]["freq"]),
    )

    dt_hours = _infer_step_hours(total["Time"])
    buy_price = _build_buy_price_series(total, config["market"])
    renewable_used_kw = total[[compatibility["load_col"], compatibility["pv_col"], "Wind_kw"]].apply(
        lambda row: min(row[compatibility["load_col"]], row[compatibility["pv_col"]] + row["Wind_kw"]),
        axis=1,
    )
    export_kw = (total[compatibility["pv_col"]] + total["Wind_kw"] - total[compatibility["load_col"]]).clip(lower=0.0)
    grid_import_kw = (total[compatibility["load_col"]] - total[compatibility["pv_col"]] - total["Wind_kw"]).clip(lower=0.0)

    annual_energy_saving = float((renewable_used_kw * buy_price * dt_hours).sum())
    annual_demand_saving = float(
        (total[compatibility["load_col"]].max() - grid_import_kw.max()) * float(config["market"]["demand_charge_rate"])
    )
    annual_export_revenue = float((export_kw * dt_hours * float(config["market"]["sell_price"])).sum())
    annual_arbitrage_revenue = 0.0

    bridge_pv_capacity_kw = float(bridge_config["pv_profile"]["capacity_kwp"])
    bridge_wind_capacity_kw = float(bridge_config["wind_profile"]["farm_capacity_mw"]) * 1000.0
    ess_capacity_kwh = float(config["cost"].get("ess_capacity_kwh", 0.0))
    total_capex = (
        bridge_pv_capacity_kw * float(config["cost"]["pv_capex_per_kw"])
        + bridge_wind_capacity_kw * float(config["cost"]["wind_capex_per_kw"])
        + ess_capacity_kwh * float(config["cost"]["ess_capex_per_kwh"])
    )
    annual_opex = total_capex * float(config["cost"]["annual_opex_ratio"])
    annualized_capex = total_capex * _capital_recovery_factor(
        float(config["cost"]["discount_rate"]),
        int(config["cost"]["asset_life_years"]),
    )
    annual_net_profit = (
        annual_energy_saving
        + annual_demand_saving
        + annual_export_revenue
        + annual_arbitrage_revenue
        - annual_opex
        - annualized_capex
    )

    summary = {
        "annual_energy_saving": annual_energy_saving,
        "annual_demand_saving": annual_demand_saving,
        "annual_export_revenue": annual_export_revenue,
        "annual_arbitrage_revenue": annual_arbitrage_revenue,
        "annual_opex": annual_opex,
        "annualized_capex": annualized_capex,
        "annual_net_profit": annual_net_profit,
    }

    if bool(config["evaluation"].get("write_result_csv", False)):
        output = _resolve_path(config["evaluation"]["result_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([summary]).to_csv(output, index=False, encoding="utf-8")

    return summary


if __name__ == "__main__":
    config = load_config(CONFIG_PATH)
    summary = run_profit_eval(config)
    logger.info("=== wind pv legacy 收益测算 ===")
    logger.info(f"config_path={CONFIG_PATH}")
    for key, value in summary.items():
        logger.info(f"{key}={value:.4f}")
