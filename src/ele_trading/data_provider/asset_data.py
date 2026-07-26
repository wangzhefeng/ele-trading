"""Active renewable and BESS asset configuration access."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ele_trading.utils.io import read_yaml


@dataclass(slots=True)
class BESSConfig:
    """Storage physical constraints and efficiency parameters."""

    asset_name: str
    soc0: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float
    eta_dis: float
    deg_cost: float
    dt: float


def load_bess_config(path: str | Path) -> BESSConfig:
    """Load active BESS parameters from YAML."""
    return BESSConfig(**read_yaml(path))
