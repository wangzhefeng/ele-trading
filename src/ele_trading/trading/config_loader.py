"""Load the one-to-one active Mengxi market configuration."""

from __future__ import annotations

from dataclasses import fields
from math import isfinite
from pathlib import Path
from typing import Any

from ele_trading.trading.contracts import MarketConfig
from ele_trading.utils.io import read_yaml


def _flatten_sections(raw: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for section_name, section in raw.items():
        if not isinstance(section, dict):
            raise ValueError(f"config section {section_name!r} must be a mapping")
        for key, value in section.items():
            if isinstance(value, dict):
                raise ValueError(
                    f"config field {section_name}.{key} must not be nested"
                )
            if key in flat:
                raise ValueError(f"duplicate config field {key!r}")
            flat[key] = value
    return flat


def load_market_config(path: str | Path) -> MarketConfig:
    """Load YAML only when its leaves map exactly to ``MarketConfig``."""
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    raw = read_yaml(config_path)
    if not isinstance(raw, dict):
        raise ValueError("market config must be a mapping")

    flat = _flatten_sections(raw)
    expected = {field.name for field in fields(MarketConfig)}
    actual = set(flat)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"MarketConfig/YAML fields differ: missing={missing}, unknown={unknown}"
        )

    config = MarketConfig(**flat)
    _validate_config(config)
    return config


def _finite_non_negative(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")


def _validate_config(config: MarketConfig) -> None:
    if config.market_name != "mengxi":
        raise ValueError("market_name must be mengxi")
    if config.settlement_mode != "mengxi_single":
        raise ValueError("settlement_mode must be mengxi_single")
    if config.dt != 0.25:
        raise ValueError("dt must be 0.25 for 15-minute trading")
    if (
        config.settle_periods <= 0
        or 96 % config.settle_periods != 0
    ):
        raise ValueError("settle_periods must be a positive divisor of 96")
    if not (
        0.0
        < config.long_recovery_lower_ratio
        < config.long_recovery_upper_ratio
    ):
        raise ValueError("invalid long-recovery ratio band")
    _finite_non_negative(
        "long_recovery_multiplier",
        config.long_recovery_multiplier,
    )
    _finite_non_negative("pos_tol_ratio", config.pos_tol_ratio)

    if config.scenario_method not in {"lhs", "mc"}:
        raise ValueError("scenario_method must be lhs or mc")
    if config.scenario_count <= 0:
        raise ValueError("scenario_count must be positive")
    if not 0.0 < config.scenario_cvar_alpha < 1.0:
        raise ValueError("scenario_cvar_alpha must be within (0, 1)")
    for name in (
        "two_stage_scenario_deviation_cost_positive",
        "two_stage_scenario_deviation_cost_negative",
        "scenario_cvar_weight",
        "operational_power_margin",
        "throughput_max_ratio",
        "deg_cost_per_mwh",
        "dr_compensation_per_mwh",
        "dr_penalty_per_mwh",
        "dr_minimum_margin",
        "dr_minimum_response_mwh",
        "monthly_trade_unit_mwh",
        "solver_time_limit_seconds",
        "solver_mip_gap",
    ):
        _finite_non_negative(name, getattr(config, name))
    if not 0.0 < config.operational_power_margin <= 1.0:
        raise ValueError("operational_power_margin must be within (0, 1]")
    if not 0 <= config.dr_window_start < config.dr_window_end <= 96:
        raise ValueError("DR window must be within the 96-period day")
    if config.dr_baseline_mode not in {"auto", "fixed"}:
        raise ValueError("dr_baseline_mode must be auto or fixed")
    if config.dr_baseline_mode == "fixed" and config.dr_baseline_mwh <= 0.0:
        raise ValueError(
            "dr_baseline_mwh must be positive when dr_baseline_mode is fixed"
        )
    if config.monthly_price_floor >= config.monthly_price_cap:
        raise ValueError("monthly price floor must be below the cap")
    if config.solver_name not in {"cbc", "glpk"}:
        raise ValueError("solver_name must be cbc or glpk")
