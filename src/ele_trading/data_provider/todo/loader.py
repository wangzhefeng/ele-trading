"""Archived investment profile and resource-simulation loaders."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ele_trading.utils.io import read_yaml

from .load_profile import LoadProfileBuildConfig
from .schemas import (
    PVProfileConfig,
    RenewableProfileResult,
    WindProfileConfig,
)


def load_load_profile(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return frame


def load_load_profile_build_config(
    path: str | Path,
) -> LoadProfileBuildConfig:
    return LoadProfileBuildConfig(**read_yaml(path))


def load_pv_profile_config(path: str | Path) -> PVProfileConfig:
    return PVProfileConfig(**read_yaml(path))


def load_wind_profile_config(path: str | Path) -> WindProfileConfig:
    return WindProfileConfig(**read_yaml(path))


def load_renewable_profile(
    path: str | Path,
    value_col: str,
) -> RenewableProfileResult:
    frame = pd.read_csv(path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    series = pd.Series(
        frame[value_col].astype(float).values,
        index=frame["timestamp"],
        name=value_col,
    )
    return RenewableProfileResult(
        power_series=series,
        metadata={"source": str(path)},
        quality_flags=None,
    )
