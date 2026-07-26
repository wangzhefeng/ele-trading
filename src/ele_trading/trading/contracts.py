"""Public contracts for the active Mengxi single-settlement chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd


@dataclass(slots=True)
class MarketConfig:
    """One-to-one active Mengxi market, operating and solver rules."""

    market_name: str = "mengxi"
    settlement_mode: str = "mengxi_single"
    settle_periods: int = 96
    dt: float = 0.25

    long_recovery_lower_ratio: float = 0.90
    long_recovery_upper_ratio: float = 1.05
    long_recovery_multiplier: float = 1.20
    long_recovery_applies_to_storage: bool = True
    pos_tol_ratio: float = 0.05

    two_stage_scenario_deviation_cost_positive: float = 0.25
    two_stage_scenario_deviation_cost_negative: float = 0.25
    scenario_method: str = "lhs"
    scenario_count: int = 20
    scenario_seed: int = 7
    scenario_cvar_alpha: float = 0.95
    scenario_cvar_weight: float = 0.0

    soc_terminal_min: float | None = None
    exclusive_charge_discharge: bool = True
    operational_power_margin: float = 0.80
    throughput_max_ratio: float = 1.0
    deg_cost_per_mwh: float = 0.0
    bess_market_role: str = "behind_meter"
    no_discharge_on_curtail: bool = False

    dr_aggregation: str = "aggregator"
    dr_compensation_per_mwh: float = 2000.0
    dr_penalty_per_mwh: float = 3000.0
    dr_minimum_margin: float = 0.0
    dr_minimum_response_mwh: float = 0.1
    dr_window_start: int = 72
    dr_window_end: int = 80

    monthly_price_floor: float = 0.0
    monthly_price_cap: float = 1500.0
    monthly_trade_unit_mwh: float = 1.0

    solver_name: str = "cbc"
    solver_time_limit_seconds: float = 30.0
    solver_mip_gap: float = 0.0


@dataclass(slots=True)
class DecisionTrace:
    """Versions and solve evidence attached to each trading decision."""

    decision_time: pd.Timestamp
    input_versions: Mapping[str, str]
    model_versions: Mapping[str, str]
    config_version: str
    solver_name: str
    solver_version: str
    solver_status: str
    objective_components: Mapping[str, float] = field(default_factory=dict)
    active_constraints: Mapping[str, tuple[int, ...]] = field(
        default_factory=dict
    )
    fallback_used: bool = False
    fallback_reason: str | None = None


@dataclass(slots=True)
class PositionState:
    """Current long-term contracts, monthly fills, budget and exposure."""

    as_of: pd.Timestamp
    q_long: pd.Series
    p_long: pd.Series
    monthly_positions: Mapping[str, float] = field(default_factory=dict)
    budget_remaining: float = 0.0
    risk_exposure: float = 0.0
    source_version: str = "unknown"


@dataclass(slots=True)
class MarketForecastBundle:
    """Aligned price, load, wind and PV forecasts from one issue time."""

    issue_time: pd.Timestamp
    price_forecast: Any
    load_forecast: Any
    wind_forecast: Any
    pv_forecast: Any


@dataclass(slots=True)
class OperationalPlan:
    """Physical next-day resource schedule with cost and risk evidence."""

    resource_schedule: pd.DataFrame
    soc: pd.Series
    expected_cost: float
    expected_risk: float
    constraint_trace: Mapping[str, tuple[int, ...]] = field(
        default_factory=dict
    )
    decision_trace: DecisionTrace | None = None


@dataclass(slots=True)
class IntradayAdjustment:
    """Change from the previously feasible remaining resource schedule."""

    p_net_new: pd.Series
    delta_p_net: pd.Series
    expected_cost_delta: float
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class IntradayPlan:
    """Executed prefix plus the latest feasible operational schedule."""

    schedule: OperationalPlan
    executed_prefix: pd.DataFrame
    adjustment: IntradayAdjustment
    fallback_used: bool = False


@dataclass(slots=True)
class SettlementReport:
    """Itemized active single-settlement result."""

    energy_cost: float
    contract_difference: float
    long_recovery: float
    dr_adjustment: float
    degradation_cost: float
    execution_adjustment: float
    total_cost: float
    baseline_cost: float
    delta_cost: float
    trace: DecisionTrace | None = None


@dataclass(slots=True)
class PositionPlan:
    """Mid-long position result without a financial day-ahead position."""

    alpha_long: float
    alpha_real: float
    q_long_monthly: pd.Series
    price_band: tuple[float, float]
    expected_cost: float
    expected_risk: float
    budget_used: float
    coverage: float


@dataclass(slots=True)
class BidLadder:
    """Monthly centralized-market order ladder."""

    direction: str
    bid_qty: list[float]
    bid_price: list[float]
    clear_prob: list[float]
    expected_cost: float
    expected_revenue: float


@dataclass(slots=True)
class CorridorAdvice:
    """Transparent fallback when orderbook data is unavailable."""

    direction: str
    qty_range: tuple[float, float]
    price_range: tuple[float, float]
    reason: str


@dataclass(slots=True)
class DRDecision:
    """Demand-response product participation decision."""

    participate: bool
    response_qty: float
    window: tuple[int, int]
    expected_compensation: float
    arbitrage_opportunity_cost: float
    expected_penalty: float
    degradation_cost: float
    net_margin: float
    fulfill_risk: str
    reject_reason: str | None = None
