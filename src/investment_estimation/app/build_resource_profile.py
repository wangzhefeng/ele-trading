from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd
from investment_estimation.utils.io import read_yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "configs" / "resource_profile_demo.yaml"


def build_resource_profile_from_paths(pv_path: str | Path, wind_path: str | Path, output_path: str | Path) -> pd.DataFrame:
    """合并 PV/Wind 单资源 CSV，输出 time,pv_kw,wind_kw。"""

    pv = _read_resource_part(pv_path, required_col="pv_kw")
    wind = _read_resource_part(wind_path, required_col="wind_kw")
    result = pv.merge(wind, on="time", how="inner").sort_values("time").reset_index(drop=True)
    if result.empty:
        raise ValueError("No overlapping timestamps between PV and wind resource files.")
    if result[["pv_kw", "wind_kw"]].isna().any().any():
        raise ValueError("Missing values found after resource profile merge.")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    result[["time", "pv_kw", "wind_kw"]].to_csv(output, index=False, encoding="utf-8")
    return result[["time", "pv_kw", "wind_kw"]]


def run_build_resource_profile(config: dict[str, Any], base_dir: Path | None = None) -> pd.DataFrame:
    """按 YAML 配置合并资源文件。"""

    root = base_dir or PACKAGE_ROOT
    paths = dict(config["paths"])
    return build_resource_profile_from_paths(
        pv_path=_resolve_path(paths["pv_csv"], root),
        wind_path=_resolve_path(paths["wind_csv"], root),
        output_path=_resolve_path(paths["output"], root),
    )


def main(config_path: str | None = None) -> None:
    """命令行入口：合并 PV/Wind 资源曲线。"""

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    config = read_yaml(path)
    df = run_build_resource_profile(config, base_dir=_config_base_dir(path))
    print(f"resource_rows={len(df)}")


def _read_resource_part(path: str | Path, required_col: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "timestamp" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"timestamp": "time"})
    missing = {"time", required_col} - set(df.columns)
    if missing:
        raise ValueError(f"{path} missing required columns: {sorted(missing)}")
    result = df[["time", required_col]].copy()
    result["time"] = pd.to_datetime(result["time"])
    if (result[required_col] < 0).any():
        raise ValueError(f"{required_col} must be non-negative.")
    return result



def _config_base_dir(path: Path) -> Path:
    config_path = path.resolve()
    return config_path.parents[1] if config_path.parent.name == "configs" else config_path.parent


def _resolve_path(path_value: str | Path, base_dir: Path) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else base_dir / path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build investment_estimation resource CSV from PV and wind CSV files.")
    parser.add_argument("--config", type=str, default=None, help="Path to YAML config.")
    args = parser.parse_args()
    main(args.config)
