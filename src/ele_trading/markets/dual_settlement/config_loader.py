"""Load and validate the dual-settlement (band-deviation) market configuration."""

from __future__ import annotations

from pathlib import Path

from ele_trading.markets.dual_settlement.contracts import MarketConfig
from ele_trading.utils.io import read_yaml


def load_market_config(path: str | Path) -> MarketConfig:
    """Load MarketConfig from YAML file with validation."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = read_yaml(path)

    # Flatten nested structure（仅结算相关段）
    flat = {}
    flat.update(raw.get("deviation", {}))
    flat.update(raw.get("mid_long", {}))
    flat.update(raw.get("market", {}))

    # Rename to match MarketConfig fields
    field_map = {
        "lam_l": "lam_l",
        "lam_u": "lam_u",
        "lam_l_long": "lam_l_long",
        "lam_u_long": "lam_u_long",
        "m_long": "m_long",
        "cpen_long_applies_to_storage": "cpen_long_applies_to_storage",
        "settlement_mode": "settlement_mode",
        "settle_periods": "settle_periods",
    }

    kwargs = {}
    for yaml_key, field_name in field_map.items():
        if yaml_key in flat:
            kwargs[field_name] = flat[yaml_key]

    config = MarketConfig(**kwargs)
    _validate_config(config)
    return config


def _validate_config(config: MarketConfig) -> None:
    """Validate configuration values."""
    if config.settlement_mode != "band_deviation":
        raise ValueError(
            f"Only band_deviation settlement supported, got {config.settlement_mode}"
        )
    if not (0 < config.lam_l < config.lam_u):
        raise ValueError(f"Invalid deviation band: [{config.lam_l}, {config.lam_u}]")
    if not (0 < config.lam_l_long < config.lam_u_long):
        raise ValueError(f"Invalid mid-long band: [{config.lam_l_long}, {config.lam_u_long}]")
    if config.settle_periods <= 0 or 96 % config.settle_periods != 0:
        raise ValueError(f"settle_periods must be a positive divisor of 96, got {config.settle_periods}")
