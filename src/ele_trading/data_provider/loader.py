from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import pandas as pd
import yaml

from .schemas import (
    CaseDataset,
    LoadProfileBuildConfig,
    PriceSeries,
    PVProfileConfig,
    RenewableProfileResult,
    ScenarioRecord,
    StorageConfig,
    WindProfileConfig,
)


def load_price_series(path: str | Path, time_col: str, price_col: str, label: str) -> PriceSeries:
    """从 CSV 读取价格序列。"""
    df = pd.read_csv(path)
    return PriceSeries(
        timestamps=df[time_col].astype(int).tolist(),
        prices=df[price_col].astype(float).tolist(),
        label=label,
    )


def load_storage_config(path: str | Path) -> StorageConfig:
    """读取储能参数配置。"""
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return StorageConfig(**data)


def load_price_scenarios(path: str | Path) -> List[ScenarioRecord]:
    """读取价格场景表。"""
    df = pd.read_csv(path)
    return [ScenarioRecord(**row) for row in df.to_dict(orient='records')]


def scenario_weights(records: Iterable[ScenarioRecord]) -> dict[str, float]:
    """汇总每个场景的权重。"""
    weights: dict[str, float] = {}
    for record in records:
        weights[record.scenario] = float(record.weight)
    return weights


def load_load_profile(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def load_load_profile_build_config(path: str | Path) -> LoadProfileBuildConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return LoadProfileBuildConfig(**data)


def load_pv_profile_config(path: str | Path) -> PVProfileConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return PVProfileConfig(**data)


def load_wind_profile_config(path: str | Path) -> WindProfileConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return WindProfileConfig(**data)


def load_renewable_profile(path: str | Path, value_col: str) -> RenewableProfileResult:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    series = pd.Series(df[value_col].astype(float).values, index=df["timestamp"], name=value_col)
    return RenewableProfileResult(power_series=series, metadata={"source": str(path)}, quality_flags=None)


def load_case_dataset(path: str | Path) -> CaseDataset:
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return CaseDataset(frame=df, metadata={"source": str(path), "rows": len(df)})
