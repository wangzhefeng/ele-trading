from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
APP_ROOT = PROJECT_ROOT / "app"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import pandas as pd

from ele_trading.optimization.interfaces import (
    UserSideDispatchPolicy,
    UserSidePVExportParams,
    UserSideBESSParams,
    UserSideWindPVBESSDispatchInput,
)
from ele_trading.optimization.user_side_wind_pv_bess_dispatch import (
    run_user_side_wind_pv_bess_dispatch,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger
from run_legacy_data_preparation import (
    build_legacy_total_frame,
    ensure_legacy_temp_data,
)


CONFIG_PATH = PROJECT_ROOT / 'configs' / 'legacy' / 'wind_pv_legacy_market_trading.yaml'


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _read_time_csv(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["Time"] = pd.to_datetime(df["Time"])
    return df


def _price_type_for_hour(hour: int, periods: list[dict]) -> str:
    for period in periods:
        if int(period["start_hour"]) <= hour < int(period["end_hour"]):
            return str(period["type"])
    raise ValueError(f"no price type configured for hour={hour}")


def _build_price_frame(index: pd.Series, price_cfg: dict) -> pd.DataFrame:
    if price_cfg["mode"] == "csv":
        price_df = pd.read_csv(_resolve_path(price_cfg["price_csv_path"]))
        price_df[price_cfg["time_col"]] = pd.to_datetime(price_df[price_cfg["time_col"]])
        price_df = price_df.rename(columns={price_cfg["time_col"]: "Time", price_cfg["price_col"]: "buy_price"})
        price_df = price_df.set_index("Time").reindex(index).interpolate(method="time").ffill().bfill().reset_index()
        price_df["price_type"] = "csv"
        return price_df

    records = []
    for timestamp in pd.to_datetime(index):
        price_type = _price_type_for_hour(timestamp.hour, price_cfg["price_type_periods"])
        records.append(
            {
                "Time": timestamp,
                "buy_price": float(price_cfg[f"{price_type}_price"]),
                "price_type": price_type,
            }
        )
    return pd.DataFrame.from_records(records)


def _build_policy(config: dict | None) -> UserSideDispatchPolicy | None:
    if config is None:
        return None
    return UserSideDispatchPolicy(
        charge_allowed_hours=config.get("charge_allowed_hours"),
        discharge_allowed_hours=config.get("discharge_allowed_hours"),
        pv_to_bess_reward_rate=float(config.get("pv_to_bess_reward_rate", 0.0)),
        pv_to_load_reward_rate=float(config.get("pv_to_load_reward_rate", 0.0)),
        pv_export_penalty_rate=float(config.get("pv_export_penalty_rate", 0.0)),
    )


def run_market_trading(config: dict) -> tuple[pd.DataFrame, object]:
    bridge_config_path = _resolve_path(config["data"]["bridge_config_path"])
    if bool(config["data"].get("refresh_before_run", False)):
        ensure_legacy_temp_data(bridge_config_path)

    bridge_config = read_yaml(bridge_config_path)
    compatibility = bridge_config["compatibility"]
    load_df = _read_time_csv(_resolve_path(config["data"]["df_2025_path"]))
    pv_df = _read_time_csv(_resolve_path(config["data"]["df_pv_2025_path"]))
    wind_df = _read_time_csv(_resolve_path(config["data"]["df_wind_2025_path"]))
    total = build_legacy_total_frame(load_df, pv_df, wind_df, compatibility, str(bridge_config["run"]["freq"]))

    start_time = pd.Timestamp(config["data"]["start_time"])
    periods = int(config["data"]["periods"])
    window = total[total["Time"] >= start_time].head(periods).copy()
    if len(window) != periods:
        raise ValueError(f"requested {periods} periods from {start_time}, got {len(window)}")

    price_frame = _build_price_frame(window["Time"], config["price"])
    renewable_forecast_kw = window["pv_kw"] + window["Wind_kw"]
    dispatch_input = UserSideWindPVBESSDispatchInput(
        timestamps=window["Time"].tolist(),
        load_forecast=window["P_kw"].astype(float).tolist(),
        pv_forecast=window["pv_kw"].astype(float).tolist(),
        wind_forecast=window["Wind_kw"].astype(float).tolist(),
        buy_price=price_frame["buy_price"].astype(float).tolist(),
        price_type=price_frame["price_type"].astype(str).tolist(),
        export=UserSidePVExportParams(
            allow_export=bool(config["export"]["allow_export"]),
            sell_price=float(config["export"]["sell_price"]),
            export_limit=float(config["export"]["export_limit"]) if config["export"]["export_limit"] is not None else None,
            curtailment_cost_rate=float(config["export"].get("curtailment_cost_rate", 0.0)),
        ),
        demand_charge_rate=float(config["dispatch"]["demand_charge_rate"]),
        step_hours=float(config["dispatch"]["step_hours"]),
        bess=UserSideBESSParams(
            capacity=float(config["bess"]["capacity"]),
            soc_min=float(config["bess"]["soc_min"]),
            soc_max=float(config["bess"]["soc_max"]),
            p_ch_max=float(config["bess"]["p_ch_max"]),
            p_dis_max=float(config["bess"]["p_dis_max"]),
            eta_ch=float(config["bess"]["eta_ch"]),
            eta_dis=float(config["bess"]["eta_dis"]),
        ),
        initial_soc=float(config["bess"]["initial_soc"]),
        terminal_soc_target=float(config["dispatch"]["terminal_soc_target"]) if config["dispatch"]["terminal_soc_target"] is not None else None,
        cycle_cost_rate=float(config["dispatch"].get("cycle_cost_rate", 0.0)),
        policy=_build_policy(config.get("policy")),
    )
    result = run_user_side_wind_pv_bess_dispatch(dispatch_input)

    result_df = pd.DataFrame(
        {
            "timestamp": window["Time"],
            "load_forecast_kw": window["P_kw"],
            "pv_forecast_kw": window["pv_kw"],
            "wind_forecast_kw": window["Wind_kw"],
            "renewable_forecast_kw": renewable_forecast_kw,
            "buy_price": price_frame["buy_price"],
            "price_type": price_frame["price_type"],
            "renewable_to_load": result.renewable_to_load,
            "renewable_to_bess": result.renewable_to_bess,
            "renewable_to_grid": result.renewable_to_grid,
            "renewable_curtailment": result.renewable_curtailment,
            "charge_power": result.charge_power,
            "discharge_power": result.discharge_power,
            "soc": result.soc,
            "grid_import": result.grid_import,
        }
    )
    if bool(config["output"].get("write_dispatch_csv", False)):
        output = _resolve_path(config["output"]["dispatch_result_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        result_df.to_csv(output, index=False, encoding="utf-8")
    return result_df, result


if __name__ == "__main__":
    config = read_yaml(CONFIG_PATH)
    result_df, result = run_market_trading(config)
    logger.info("=== wind pv legacy market trading ===")
    logger.info(f"config_path={CONFIG_PATH}")
    logger.info(f"energy_cost={result.energy_cost:.4f}")
    logger.info(f"demand_cost={result.demand_cost:.4f}")
    logger.info(f"sell_revenue={result.sell_revenue:.4f}")
    logger.info(f"curtailment_cost={result.curtailment_cost:.4f}")
    logger.info(f"cycle_cost={result.cycle_cost:.4f}")
    logger.info(f"total_cost={result.total_cost:.4f}")
    logger.info(f"max_grid_import={result.max_grid_import:.4f}")
    logger.info(f"constraint_violations={result.constraint_violations}")
    logger.info(f"\n{result_df.to_string(index=False)}")
