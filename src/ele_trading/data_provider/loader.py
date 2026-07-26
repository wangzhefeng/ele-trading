"""Deprecated generic loader imports.

New code should use ``market_data`` and ``asset_data`` directly. This module
contains no investment profile or resource-capacity semantics.
"""

from .asset_data import BESSConfig, load_bess_config
from .market_data import (
    load_observed_power_series,
    load_price_scenarios,
    load_price_series,
    scenario_weights,
)

__all__ = [
    "BESSConfig",
    "load_bess_config",
    "load_observed_power_series",
    "load_price_scenarios",
    "load_price_series",
    "scenario_weights",
]
