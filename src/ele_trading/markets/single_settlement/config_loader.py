"""Load the sectioned single-settlement market configuration (schema v1).

v3 M2（D-003）：YAML 按 market/scenario/bess/dr/monthly/solver 六个区段
装配到同名 typed config 子对象；字段级取值校验在各子对象
``__post_init__`` 中完成；旧扁平格式经
``scripts/migrate_market_config_v3.py`` 转换，不做双格式兼容。
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

from ele_trading.markets.sections import (
    CURRENT_SCHEMA_VERSION,
    BessSection,
    DrSection,
    MarketConfig,
    MarketSection,
    MonthlySection,
    ScenarioSection,
    SolverSection,
)
from ele_trading.utils.io import read_yaml

SECTION_TYPES = {
    "market": MarketSection,
    "scenario": ScenarioSection,
    "bess": BessSection,
    "dr": DrSection,
    "monthly": MonthlySection,
    "solver": SolverSection,
}


def load_market_config(path: str | Path) -> MarketConfig:
    """Load YAML only when its sections map exactly to the typed sections."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    raw = read_yaml(config_path)
    if not isinstance(raw, dict):
        raise ValueError("market config must be a mapping")

    version = raw.get("schema_version")
    if version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be {CURRENT_SCHEMA_VERSION}; "
            f"got {version!r} (legacy flat configs must be migrated with "
            "scripts/migrate_market_config_v3.py)"
        )

    section_names = set(raw) - {"schema_version"}
    expected_names = set(SECTION_TYPES)
    if section_names != expected_names:
        missing = sorted(expected_names - section_names)
        unknown = sorted(section_names - expected_names)
        raise ValueError(
            f"config sections differ: missing={missing}, unknown={unknown}"
        )

    sections: dict[str, Any] = {}
    for name, section_type in SECTION_TYPES.items():
        section_raw = raw[name]
        if not isinstance(section_raw, dict):
            raise ValueError(f"config section {name!r} must be a mapping")
        expected_fields = {field.name for field in fields(section_type)}
        actual_fields = set(section_raw)
        if actual_fields != expected_fields:
            missing = sorted(expected_fields - actual_fields)
            unknown = sorted(actual_fields - expected_fields)
            raise ValueError(
                f"config section {name!r} fields differ: "
                f"missing={missing}, unknown={unknown}"
            )
        sections[name] = section_type(**section_raw)

    config = MarketConfig(schema_version=version, **sections)
    # ---------------- 单结算模式身份校验（插件专属，不上移到共享词汇） ----------------
    if config.market.market_name != "single_settlement":
        raise ValueError("market_name must be single_settlement")
    if config.market.settlement_mode != "single_settlement":
        raise ValueError("settlement_mode must be single_settlement")
    return config
