"""风电仿真 v1 运行脚本（自定义功率曲线 + 满发小时数校准）

从 configs/wind_simulation_v1.yaml 加载参数，
从 Open-Meteo 获取气象数据，使用 windpowerlib 生成风电出力时序。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import argparse

import pandas as pd

from ele_trading.resource_simulation import WindProfileConfig, load_or_build_wind_profile
from ele_trading.data_provider.resource_weather import fetch_weather_open_meteo
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger


DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'configs' / 'wind_simulation_v1.yaml'


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def run_wind_simulation_v1(config: dict[str, Any]) -> pd.DataFrame:
    run_cfg = config["run"]
    paths = config["paths"]
    wind_cfg_dict = dict(config["wind_simulation"])
    site = config["site"]

    target_year = int(run_cfg["target_year"])
    wind_cfg_dict["year"] = int(wind_cfg_dict.get("year", target_year))
    wind_cfg = WindProfileConfig(**wind_cfg_dict)

    # 获取气象数据
    weather_df = fetch_weather_open_meteo(
        latitude=float(site["latitude"]),
        longitude=float(site["longitude"]),
        start_date=f"{target_year}-01-01",
        end_date=f"{target_year}-12-31",
        hourly_fields=["wind_speed_100m", "temperature_2m"],
    ).set_index("timestamp")

    result = load_or_build_wind_profile(config=wind_cfg, weather_df=weather_df)

    # 输出为 timestamp, wind_power_mw 格式
    df = result.power_series.rename("wind_power_mw").to_frame().reset_index()
    df.columns = ["timestamp", "wind_power_mw"]

    output_path = _resolve_path(paths["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    logger.info("wind simulation v1 output: %s (%d rows)", output_path, len(df))
    return df


def main(config_path: str | None = None) -> None:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = read_yaml(path)

    site = config.get("site", {})
    logger.info(
        "starting wind simulation v1: site=(%.2f, %.2f) year=%s",
        float(site.get("latitude", 0)),
        float(site.get("longitude", 0)),
        config.get("run", {}).get("target_year"),
    )

    df = run_wind_simulation_v1(config)
    logger.info("wind simulation v1 complete: %d rows", len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run wind simulation v1 (custom power curve)")
    parser.add_argument("--config", type=str, default=None, help="Path to config YAML")
    args = parser.parse_args()
    main(args.config)
