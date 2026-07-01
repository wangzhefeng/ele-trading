"""光伏仿真 v2 运行脚本（气象驱动 + 等效小时数校准）

从 configs/resource_simulation/pv_simulation_v2.yaml 加载参数，
从 Open-Meteo 获取气象数据，使用 pvlib 物理仿真生成光伏出力时序。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import argparse

import pandas as pd

from ele_trading.capacity_planning.resource_simulation import PVSimulator
from ele_trading.data_provider.resource_weather import fetch_weather_open_meteo
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger


DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'configs' / 'resource_simulation' / 'pv_simulation_v2.yaml'


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def run_pv_simulation_v2(config: dict[str, Any]) -> pd.DataFrame:
    run_cfg = config["run"]
    paths = config["paths"]
    pv_cfg = config["pv_simulation"]
    site = config["site"]

    target_year = int(run_cfg["target_year"])

    # 获取气象数据
    weather_df = fetch_weather_open_meteo(
        latitude=float(site["latitude"]),
        longitude=float(site["longitude"]),
        start_date=f"{target_year}-01-01",
        end_date=f"{target_year}-12-31",
        hourly_fields=["ghi", "temp_air", "wind_speed"],
    )

    # 确保索引为 DatetimeIndex
    if "timestamp" in weather_df.columns:
        weather_df = weather_df.set_index(pd.to_datetime(weather_df["timestamp"]))

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

    # 输出为 timestamp, pv_kw 格式
    df = result.power_series.rename("pv_kw").to_frame().reset_index()
    df.columns = ["timestamp", "pv_kw"]

    output_path = _resolve_path(paths["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    logger.info("pv simulation v2 output: %s (%d rows)", output_path, len(df))
    return df


def main(config_path: str | None = None) -> None:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = read_yaml(path)

    site = config.get("site", {})
    logger.info(
        "starting pv simulation v2: site=(%.2f, %.2f) year=%s",
        float(site.get("latitude", 0)),
        float(site.get("longitude", 0)),
        config.get("run", {}).get("target_year"),
    )

    df = run_pv_simulation_v2(config)
    logger.info("pv simulation v2 complete: %d rows", len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PV simulation v2 (weather-driven)")
    parser.add_argument("--config", type=str, default=None, help="Path to config YAML")
    args = parser.parse_args()
    main(args.config)
