from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


OPTIMIZATION_PATH = Path(__file__).resolve().parent / "optimization.py"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "pv_es_calc.yaml"


OUTPUT_COLUMN_CN = {
    "revenue": "收益",
    "baseline_cost": "光伏基准净成本",
    "opt_cost": "光伏储能优化净成本",
    "baseline_energy_cost": "光伏基准购电电费",
    "baseline_pv_sell_revenue": "光伏基准上网收益",
    "baseline_max_demand_cost": "光伏基准需量电费",
    "load_only_max_demand_cost": "无光伏无储能需量电费",
    "energy_cost": "优化后购电电费",
    "pv_sell_revenue": "优化后光伏上网收益",
    "max_demand_cost": "优化后需量电费",
    "max_demand_cost_delta": "需量电费变化",
    "grid_import_energy": "电网购电量",
    "grid_to_battery_energy": "电网充储电量",
    "pv_to_battery_energy": "光伏充储电量",
    "battery_discharge_energy": "储能放电量",
    "pv_to_grid_energy": "光伏上网电量",
}


def _load_optimization_module():
    spec = importlib.util.spec_from_file_location("pv_es_optimization_entry", OPTIMIZATION_PATH)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"cannot load optimization module from {OPTIMIZATION_PATH}")
    spec.loader.exec_module(module)
    return module


def with_chinese_output_columns(result_df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        col: f"{col}_{OUTPUT_COLUMN_CN[col]}"
        for col in result_df.columns
        if col in OUTPUT_COLUMN_CN
    }
    return result_df.rename(columns=rename_map)


def calculate_monthly_demand_cost(grid_import: pd.Series, max_demand_price: float) -> float:
    monthly_max = grid_import.resample("ME").max()
    return float(monthly_max.sum() * max_demand_price)


def build_pv_only_baseline(demand_load_df: pd.DataFrame, pv_load_df: pd.DataFrame) -> pd.DataFrame:
    demand = demand_load_df["demand_load"].to_numpy(dtype=float)
    pv = pv_load_df["pv_load"].to_numpy(dtype=float)
    pv_to_load = np.minimum(demand, pv)
    pv_to_grid = np.maximum(pv - demand, 0.0)
    grid_import = np.maximum(demand - pv, 0.0)
    return pd.DataFrame(
        {
            "pv_to_load": pv_to_load,
            "pv_to_grid": pv_to_grid,
            "grid_import": grid_import,
        },
        index=demand_load_df.index,
    )


def calculate_opt_cost(
    strategy_df: pd.DataFrame,
    ele_price_df: pd.DataFrame,
    max_demand_price: float,
    pv_sell_price: float,
    time_ratio: float,
) -> dict[str, float]:
    energy_cost = float((strategy_df["grid_import"] * ele_price_df["ele_price"]).sum() * time_ratio)
    pv_sell_revenue = float(strategy_df["pv_to_grid"].sum() * pv_sell_price * time_ratio)
    max_demand_cost = calculate_monthly_demand_cost(strategy_df["grid_import"], max_demand_price)
    return {
        "energy_cost": energy_cost,
        "pv_sell_revenue": pv_sell_revenue,
        "max_demand_cost": max_demand_cost,
        "net_cost": energy_cost + max_demand_cost - pv_sell_revenue,
    }


def validate_strategy_detail(
    demand_load_df: pd.DataFrame,
    pv_load_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
    tol: float = 1e-3,
) -> None:
    np.testing.assert_allclose(
        strategy_df["pv_to_load"] + strategy_df["pv_to_battery"] + strategy_df["pv_to_grid"],
        pv_load_df["pv_load"],
        atol=tol,
    )
    np.testing.assert_allclose(
        strategy_df["pv_to_load"] + strategy_df["battery_discharge"] + strategy_df["grid_to_load"],
        demand_load_df["demand_load"],
        atol=tol,
    )
    np.testing.assert_allclose(
        strategy_df["grid_import"],
        strategy_df["grid_to_load"] + strategy_df["grid_to_battery"],
        atol=tol,
    )


def simulate_one_scale(
    es_scale: float,
    config: dict[str, Any],
    method_version: str | None = None,
) -> dict[str, float]:
    optimization = _load_optimization_module()
    method = method_version or config["run"]["method_version"]
    frames = optimization.load_pv_es_input_data(config)
    strategy_path = (
        optimization.strategy_output_dir(config, method)
        / f"schedule_result_scale_{_scale_label(es_scale)}.csv"
    )
    strategy_df = pd.read_csv(strategy_path)
    strategy_df["time"] = pd.to_datetime(strategy_df["time"])
    strategy_df.set_index("time", inplace=True)

    data = frames["demand"].join(frames["pv"], how="inner").join(frames["price"], how="inner")
    strategy_df = strategy_df.loc[data.index]
    demand_df = data[["demand_load"]]
    pv_df = data[["pv_load"]]
    price_df = data[["ele_price", "ele_type"]]
    validate_strategy_detail(demand_df, pv_df, strategy_df)

    time_ratio = int(config["run"]["freq_minutes"]) / 60
    max_demand_price = float(config["market"]["max_demand_price"])
    pv_sell_price = float(config["market"]["pv_sell_price"])
    baseline_df = build_pv_only_baseline(demand_df, pv_df)
    load_only_max_demand_cost = calculate_monthly_demand_cost(demand_df["demand_load"], max_demand_price)
    baseline_cost = calculate_opt_cost(baseline_df, price_df, max_demand_price, pv_sell_price, time_ratio)
    opt_cost = calculate_opt_cost(strategy_df, price_df, max_demand_price, pv_sell_price, time_ratio)
    revenue = baseline_cost["net_cost"] - opt_cost["net_cost"]
    return {
        "es_scale": float(es_scale),
        "revenue": revenue,
        "baseline_cost": baseline_cost["net_cost"],
        "opt_cost": opt_cost["net_cost"],
        "baseline_energy_cost": baseline_cost["energy_cost"],
        "baseline_pv_sell_revenue": baseline_cost["pv_sell_revenue"],
        "baseline_max_demand_cost": baseline_cost["max_demand_cost"],
        "load_only_max_demand_cost": load_only_max_demand_cost,
        "energy_cost": opt_cost["energy_cost"],
        "pv_sell_revenue": opt_cost["pv_sell_revenue"],
        "max_demand_cost": opt_cost["max_demand_cost"],
        "max_demand_cost_delta": opt_cost["max_demand_cost"] - baseline_cost["max_demand_cost"],
        "grid_import_energy": float(strategy_df["grid_import"].sum() * time_ratio),
        "grid_to_battery_energy": float(strategy_df["grid_to_battery"].sum() * time_ratio),
        "pv_to_battery_energy": float(strategy_df["pv_to_battery"].sum() * time_ratio),
        "battery_discharge_energy": float(strategy_df["battery_discharge"].sum() * time_ratio),
        "pv_to_grid_energy": float(strategy_df["pv_to_grid"].sum() * time_ratio),
    }


def run_simulation_summary(
    config: dict[str, Any],
    method_version: str | None = None,
) -> pd.DataFrame:
    optimization = _load_optimization_module()
    method = method_version or config["run"]["method_version"]
    rows = [simulate_one_scale(scale, config, method) for scale in config["run"]["es_scale_list"]]
    summary_df = pd.DataFrame(rows).set_index("es_scale")
    output_path = optimization.strategy_result_root(config, method) / "estimate_result_scale_all_optim.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with_chinese_output_columns(summary_df).to_csv(output_path, encoding="utf-8-sig")
    if config.get("plot", {}).get("enabled", False):
        _plot_strategy_details(config, method, optimization)
    return summary_df


def _plot_strategy_details(config: dict[str, Any], method_version: str, optimization) -> None:
    from utils.pv_es_plot import plot_strategy_power_detail

    frames = optimization.load_pv_es_input_data(config)
    plot_config = config.get("plot", {})
    output_dir = optimization._resolve_path(plot_config.get("output_dir", "data/profit_calc/pv_es/plots"))
    start_time = plot_config.get("start_time")
    end_time = plot_config.get("end_time")
    date = plot_config.get("date")

    demand_df = frames["demand"].rename(columns={"demand_load": "value"})
    pv_df = frames["pv"].rename(columns={"pv_load": "value"})
    price_df = frames["price"].rename(columns={"ele_price": "value", "ele_type": "type"})

    for es_scale in config["run"]["es_scale_list"]:
        scale = float(es_scale)
        strategy_path = (
            optimization.strategy_output_dir(config, method_version)
            / f"schedule_result_scale_{_scale_label(scale)}.csv"
        )
        strategy_df = pd.read_csv(strategy_path)
        strategy_df["time"] = pd.to_datetime(strategy_df["time"])
        strategy_df.set_index("time", inplace=True)
        save_path = output_dir / method_version / f"strategy_detail_scale_{_scale_label(scale)}.png"
        plot_strategy_power_detail(
            demand_load_df=demand_df,
            pv_load_df=pv_df,
            ele_price_df=price_df,
            strategy_df=strategy_df,
            es_scale=scale,
            title=f"PV + ES Strategy Detail - {method_version} - ES {_scale_label(scale)} kW",
            save_path=save_path,
            start_time=start_time,
            end_time=end_time,
            date=date,
        )


def _scale_label(es_scale: float) -> str:
    return str(int(es_scale)) if float(es_scale).is_integer() else str(es_scale)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unified PV-storage simulation.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--method-version", default=None)
    args = parser.parse_args()
    optimization = _load_optimization_module()
    config = optimization.load_pv_es_config(args.config)
    method = args.method_version or config["run"]["method_version"]
    optimization.run_capacity_search(config, method)
    run_simulation_summary(config, method)
    print(f"pv_es_calc simulation finished: method_version={method}")


if __name__ == "__main__":
    main()
