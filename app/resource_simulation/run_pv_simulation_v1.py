"""光伏仿真 v1 运行脚本（clear-sky 模式）

从 configs/resource_simulation/pv_simulation_v1.yaml 加载参数，
使用 pvlib clear-sky 模型生成光伏出力时序。
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

from ele_trading.resource_simulation import PVProfileConfig, load_or_build_pv_profile
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger


DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'configs' / 'resource_simulation' / 'pv_simulation_v1.yaml'


def _resolve_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def run_pv_simulation_v1(config: dict[str, Any]) -> pd.DataFrame:
    run_cfg = config["run"]
    paths = config["paths"]
    pv_cfg_dict = dict(config["pv_simulation"])

    pv_cfg = PVProfileConfig(**pv_cfg_dict)

    # 生成全年时间索引
    target_year = int(run_cfg["target_year"])
    freq = str(run_cfg["freq"])
    time_index = pd.date_range(
        start=f"{target_year}-01-01",
        end=f"{target_year}-12-31 23:45:00",
        freq=freq,
    )

    result = load_or_build_pv_profile(config=pv_cfg, time_index=time_index)

    # 输出为 timestamp, pv_kw 格式
    df = result.power_series.rename("pv_kw").to_frame().reset_index()
    df.columns = ["timestamp", "pv_kw"]

    output_path = _resolve_path(paths["output"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False, encoding="utf-8")
    logger.info("pv simulation v1 output: %s (%d rows)", output_path, len(df))
    return df


def main(config_path: str | None = None) -> None:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = read_yaml(path)

    site = config.get("site", {})
    logger.info(
        "starting pv simulation v1: site=(%.2f, %.2f) year=%s",
        float(site.get("latitude", 0)),
        float(site.get("longitude", 0)),
        config.get("run", {}).get("target_year"),
    )

    df = run_pv_simulation_v1(config)
    logger.info("pv simulation v1 complete: %d rows", len(df))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PV simulation v1 (clear-sky)")
    parser.add_argument("--config", type=str, default=None, help="Path to config YAML")
    args = parser.parse_args()
    main(args.config)
