from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from invest_est_models.resource_simulation import PVProfileConfig, load_or_build_pv_profile, load_weather_csv


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "configs" / "resource_pv_simulation_v1.yaml"


def run_pv_simulation_v1(config: dict[str, Any], base_dir: Path | None = None) -> pd.DataFrame:
    """按 YAML 配置运行光伏 v1 仿真，并输出 time,pv_kw。"""

    root = base_dir or PACKAGE_ROOT
    run_cfg = dict(config["run"])
    paths = dict(config["paths"])
    pv_cfg = PVProfileConfig(**dict(config["pv_simulation"]))
    target_year = int(run_cfg["target_year"])
    freq = str(run_cfg["freq"])
    time_index = pd.date_range(
        start=f"{target_year}-01-01",
        end=f"{target_year}-12-31 23:45:00",
        freq=freq,
    )
    weather_df = _load_optional_weather(paths, root)

    result = load_or_build_pv_profile(config=pv_cfg, time_index=time_index, weather_df=weather_df)
    df = result.power_series.rename("pv_kw").to_frame().reset_index()
    df.columns = ["time", "pv_kw"]
    output_path = _resolve_path(paths["output"], root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    return df


def main(config_path: str | None = None) -> None:
    """命令行入口：运行光伏 v1 资源仿真。"""

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = _read_yaml(path)
    df = run_pv_simulation_v1(config, base_dir=_config_base_dir(path))
    print(f"pv_rows={len(df)}")


def _load_optional_weather(paths: dict[str, Any], base_dir: Path) -> pd.DataFrame | None:
    weather_path = paths.get("weather_input")
    if not weather_path:
        return None
    df = load_weather_csv(_resolve_path(weather_path, base_dir), time_col=str(paths.get("weather_time_col", "timestamp")))
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
    parser = argparse.ArgumentParser(description="Run invest_est_models PV simulation v1.")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config.")
    args = parser.parse_args()
    main(args.config)
