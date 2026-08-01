from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
from investment_estimation.utils.io import read_yaml

from investment_estimation.resource_simulation import PVSimulator, fetch_weather_open_meteo, load_weather_csv


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "configs" / "resource_pv_simulation_v2.yaml"


def run_pv_simulation_v2(config: dict[str, Any], base_dir: Path | None = None) -> pd.DataFrame:
    """按 YAML 配置运行光伏 v2 仿真，并输出 time,pv_kw。"""

    root = base_dir or PACKAGE_ROOT
    site = dict(config["site"])
    run_cfg = dict(config["run"])
    paths = dict(config["paths"])
    pv_cfg = dict(config["pv_simulation"])
    weather_df = _load_weather(config, root)
    simulator = PVSimulator(
        latitude=float(pv_cfg["latitude"]),
        longitude=float(pv_cfg["longitude"]),
        timezone=str(pv_cfg.get("timezone", "Asia/Shanghai")),
        tilt=pv_cfg.get("tilt"),
        azimuth=float(pv_cfg.get("azimuth", 180.0)),
        altitude=float(pv_cfg.get("altitude", 0.0)),
    )
    result = simulator.simulate(
        weather_df=weather_df,
        equiv_hours=float(pv_cfg["equiv_hours"]),
        target_capacity_mw=float(pv_cfg["target_capacity_mw"]),
    )
    df = result.power_series.rename("pv_kw").to_frame().reset_index()
    df.columns = ["time", "pv_kw"]
    output_path = _resolve_path(paths["output"], root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    return df


def main(config_path: str | None = None) -> None:
    """命令行入口：运行光伏 v2 资源仿真。"""

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = read_yaml(path)
    df = run_pv_simulation_v2(config, base_dir=_config_base_dir(path))
    print(f"pv_rows={len(df)}")


def _load_weather(config: dict[str, Any], base_dir: Path) -> pd.DataFrame:
    paths = dict(config["paths"])
    weather_path = paths.get("weather_input")
    if weather_path:
        df = load_weather_csv(_resolve_path(weather_path, base_dir), time_col=str(paths.get("weather_time_col", "timestamp")))
    else:
        site = dict(config["site"])
        run_cfg = dict(config["run"])
        target_year = int(run_cfg["target_year"])
        df = fetch_weather_open_meteo(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start_date=f"{target_year}-01-01",
            end_date=f"{target_year}-12-31",
            hourly_fields=list(run_cfg.get("hourly_fields", ["ghi", "temp_air", "wind_speed"])),
        )
    return df.set_index(pd.to_datetime(df["timestamp"]))



def _config_base_dir(path: Path) -> Path:
    config_path = path.resolve()
    return config_path.parents[1] if config_path.parent.name == "configs" else config_path.parent


def _resolve_path(path_value: str | Path, base_dir: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else base_dir / path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run investment_estimation PV simulation v2.")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config.")
    args = parser.parse_args()
    main(args.config)
