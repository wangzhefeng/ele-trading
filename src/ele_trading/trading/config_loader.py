"""Load and validate Mengxi market configuration."""

from __future__ import annotations

from pathlib import Path

from ele_trading.trading.contracts import MarketConfig
from ele_trading.utils.io import read_yaml


def load_market_config(path: str | Path) -> MarketConfig:
    """Load MarketConfig from YAML file with validation."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = read_yaml(path)

    # Flatten nested structure
    flat = {}
    flat.update(raw.get("deviation", {}))
    flat.update(raw.get("bid", {}))
    flat.update(raw.get("strategy", {}).get("weights", {}))
    flat["strategy"] = raw.get("strategy", {}).get("default", "BALANCED")
    flat.update(raw.get("bess", {}))
    flat.update(raw.get("mid_long", {}))
    flat.update(raw.get("dr", {}))
    flat.update(raw.get("forecast", {}))
    flat.update(raw.get("market", {}))

    # Rename to match MarketConfig fields
    field_map = {
        "lam_l": "lam_l",
        "lam_u": "lam_u",
        "lam_l_long": "lam_l_long",
        "lam_u_long": "lam_u_long",
        "m_long": "m_long",
        "gap": "gap",
        "bias_k": "bias_k",
        "price_floor": "price_floor",
        "price_cap": "price_cap",
        "w_bes": "w_bes",
        "w_pen": "w_pen",
        "w_ecost": "w_ecost",
        "w_xu": "w_xu",
        "w_dr": "w_dr",
        "strategy": "strategy",
        "settlement_mode": "settlement_mode",
        "settle_periods": "settle_periods",
        "soc_terminal_min": "soc_terminal_min",
        "exclusive_charge_discharge": "exclusive_charge_discharge",
        "dayahead_power_margin": "dayahead_power_margin",
        "throughput_max_ratio": "throughput_max_ratio",
        "deg_cost_per_mwh": "deg_cost_per_mwh",
        "market_role": "bess_market_role",
        "no_discharge_on_curtail": "no_discharge_on_curtail",
        "pos_tol_ratio": "pos_tol_ratio",
        "cpen_long_applies_to_storage": "cpen_long_applies_to_storage",
        "aggregation": "dr_aggregation",
        "sca_price": "sca_price",
        "sca_power": "sca_power",
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
    if config.settlement_mode != "mengxi_band":
        raise ValueError(f"Only mengxi_band settlement supported, got {config.settlement_mode}")
    if not (0 < config.lam_l < config.lam_u):
        raise ValueError(f"Invalid deviation band: [{config.lam_l}, {config.lam_u}]")
    if not (0 < config.lam_l_long < config.lam_u_long):
        raise ValueError(f"Invalid mid-long band: [{config.lam_l_long}, {config.lam_u_long}]")
    if config.price_floor >= config.price_cap:
        raise ValueError(f"Invalid price limits: [{config.price_floor}, {config.price_cap}]")
    if not (0 < config.dayahead_power_margin <= 1.0):
        raise ValueError(f"dayahead_power_margin must be in (0,1], got {config.dayahead_power_margin}")
