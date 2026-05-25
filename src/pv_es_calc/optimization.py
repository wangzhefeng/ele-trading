from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "pv_es_calc.yaml"
SCHEDULER_PATH = (
    Path(__file__).resolve().parent
    / "optimization"
    / "EsArbitraryRangeScheduler_withMaxDemand.py"
)


def _load_scheduler_class():
    spec = importlib.util.spec_from_file_location("pv_es_unified_scheduler", SCHEDULER_PATH)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"cannot load scheduler from {SCHEDULER_PATH}")
    spec.loader.exec_module(module)
    return module.EsArbitraryRangeScheduler_withMaxDemand


def load_pv_es_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("pv_es_calc config must be a mapping")
    return config


def load_pv_es_input_data(config: dict[str, Any]) -> dict[str, pd.DataFrame]:
    data_config = config["data"]
    data_dir = _resolve_path(data_config["data_dir"])
    encoding = data_config.get("encoding", "utf-8-sig")
    time_col = data_config.get("time_col", "time")
    value_col = data_config.get("value_col", "value")
    price_type_col = data_config.get("price_type_col", "type")

    demand = _read_time_series(data_dir / data_config["demand_file"], encoding, time_col)
    pv = _read_time_series(data_dir / data_config["pv_file"], encoding, time_col)
    price = _read_time_series(data_dir / data_config["price_file"], encoding, time_col)

    demand = demand.rename(columns={value_col: "demand_load"})
    pv = pv.rename(columns={value_col: "pv_load"})
    price = price.rename(columns={value_col: "ele_price", price_type_col: "ele_type"})

    run_config = config["run"]
    start_time = pd.to_datetime(run_config["start_time"])
    end_time = pd.to_datetime(run_config["end_time"])
    frames = {}
    for name, frame in {"demand": demand, "pv": pv, "price": price}.items():
        frames[name] = frame[(frame.index >= start_time) & (frame.index < end_time)].copy()
    return frames


def build_devices_info(es_scale: float, config: dict[str, Any]) -> list[dict[str, float]]:
    storage = config["storage"]
    return [
        {
            "usable_depth": float(storage["usable_depth"]),
            "charge_loss": float(storage["charge_loss"]),
            "discharge_loss": float(storage["discharge_loss"]),
            "es_charge_max": float(es_scale),
            "es_charge_min": -float(es_scale),
            "es_capacity_max": float(es_scale) * float(storage["capacity_hours"]),
            "es_capacity_min": float(storage["es_capacity_min"]),
            "transform_capacity": float(storage["transform_capacity"]),
        }
    ]


def run_one_scale(
    es_scale: float,
    config: dict[str, Any],
    method_version: str | None = None,
) -> pd.DataFrame:
    method = method_version or config["run"]["method_version"]
    frames = load_pv_es_input_data(config)
    data = (
        frames["demand"]
        .join(frames["pv"], how="inner")
        .join(frames["price"], how="inner")
    )
    scheduler_cls = _load_scheduler_class()
    scheduler = scheduler_cls(
        schedule_time_range=data.index.tolist(),
        demand_load=data["demand_load"].tolist(),
        ele_prices=data["ele_price"].tolist(),
        ele_types=data["ele_type"].tolist(),
        pv_load=data["pv_load"].tolist(),
        devices_info=build_devices_info(es_scale, config),
        current_soc_list=[float(config["storage"]["initial_soc"])],
        max_demand_price=float(config["market"]["max_demand_price"]),
        freq_minutes=int(config["run"]["freq_minutes"]),
        method_version=method,
        pv_sell_price=float(config["market"]["pv_sell_price"]),
        **config["objective"],
    )
    result = scheduler.run()[0]
    result["time"] = result.index
    _strategy_output_path(config, method, es_scale).parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(_strategy_output_path(config, method, es_scale), index=False)
    return result


def run_capacity_search(
    config: dict[str, Any],
    method_version: str | None = None,
) -> list[tuple[float, pd.DataFrame]]:
    method = method_version or config["run"]["method_version"]
    results = []
    for es_scale in config["run"]["es_scale_list"]:
        scale = float(es_scale)
        results.append((scale, run_one_scale(scale, config, method)))
    return results


def strategy_output_dir(config: dict[str, Any], method_version: str) -> Path:
    return (
        _resolve_path(config["run"]["output_dir"])
        / f"opt_result-{method_version}"
        / config["run"].get("strategy_dir", "es_scale_experiment_optim")
    )


def strategy_result_root(config: dict[str, Any], method_version: str) -> Path:
    return _resolve_path(config["run"]["output_dir"]) / f"opt_result-{method_version}"


def _strategy_output_path(config: dict[str, Any], method_version: str, es_scale: float) -> Path:
    return strategy_output_dir(config, method_version) / f"schedule_result_scale_{_scale_label(es_scale)}.csv"


def _read_time_series(path: Path, encoding: str, time_col: str) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding=encoding)
    frame[time_col] = pd.to_datetime(frame[time_col])
    frame.set_index(time_col, inplace=True)
    frame.sort_index(inplace=True)
    return frame


def _resolve_path(path: str | Path) -> Path:
    path = Path(path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _scale_label(es_scale: float) -> str:
    return str(int(es_scale)) if float(es_scale).is_integer() else str(es_scale)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run unified PV-storage optimization.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--method-version", default=None)
    args = parser.parse_args()
    config = load_pv_es_config(args.config)
    method = args.method_version or config["run"]["method_version"]
    run_capacity_search(config, method)
    print(f"pv_es_calc optimization finished: method_version={method}")


if __name__ == "__main__":
    main()
