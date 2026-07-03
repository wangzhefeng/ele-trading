from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from invest_est_models.resource_simulation import WindProfileConfig, fetch_weather_open_meteo, load_or_build_wind_profile, load_weather_csv


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "configs" / "resource_wind_simulation_v1.yaml"


def run_wind_simulation_v1(config: dict[str, Any], base_dir: Path | None = None) -> pd.DataFrame:
    """按 YAML 配置运行风电 v1 仿真，并输出 time,wind_kw。"""

    root = base_dir or PACKAGE_ROOT
    run_cfg = dict(config["run"])
    wind_cfg_dict = dict(config["wind_simulation"])
    target_year = int(run_cfg["target_year"])
    wind_cfg_dict["year"] = int(wind_cfg_dict.get("year", target_year))
    wind_cfg = WindProfileConfig(**wind_cfg_dict)
    weather_df = _load_weather(config, root)
    result = load_or_build_wind_profile(config=wind_cfg, weather_df=weather_df)
    df = result.power_series.rename("wind_kw").to_frame().reset_index()
    df.columns = ["time", "wind_kw"]
    output_path = _resolve_path(config["paths"]["output"], root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    return df


def main(config_path: str | None = None) -> None:
    """命令行入口：运行风电 v1 资源仿真。"""

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = _read_yaml(path)
    df = run_wind_simulation_v1(config, base_dir=_config_base_dir(path))
    print(f"wind_rows={len(df)}")


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
            hourly_fields=["wind_speed_100m", "temperature_2m"],
        )
    return df.set_index(pd.to_datetime(df["timestamp"]))


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _config_base_dir(path: Path) -> Path:
    config_path = path.resolve()
    return config_path.parents[1] if config_path.parent.name == "configs" else config_path.parent


def _resolve_path(path_value: str | Path, base_dir: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else base_dir / path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run invest_est_models wind simulation v1.")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config.")
    args = parser.parse_args()
    main(args.config)
