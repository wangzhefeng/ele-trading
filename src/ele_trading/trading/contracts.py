"""Data contracts for the Mengxi electricity-trading main line.

All dataclasses use ``slots=True`` for memory efficiency and immutability-by-default.
Column names in DataFrames/Series must match the canonical symbols defined in the
v1 design document §4.2 (e.g. ``p_dayah``, ``Q_real``, ``soc``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MarketConfig:
    """Mengxi market rules and strategy weights (§5.1, §11.1)."""

    # Deviation bands
    lam_l: float = 0.95
    lam_u: float = 1.05
    lam_l_long: float = 0.90
    lam_u_long: float = 1.05
    m_long: float = 1.2

    # Bid / risk
    gap: float = 3.0
    bias_k: int = 1
    price_floor: float = 0.0
    price_cap: float = 1500.0

    # Strategy weights
    w_bes: float = 1.0
    w_pen: float = 1.0
    w_ecost: float = 0.0
    w_xu: float = 0.0
    w_dr: float = 0.0
    strategy: str = "BALANCED"

    # Settlement
    settlement_mode: str = "mengxi_band"  # only "mengxi_band" supported
    settle_periods: int = 96

    # BESS operational
    soc_terminal_min: float | None = None  # None → use socini
    exclusive_charge_discharge: bool = False
    dayahead_power_margin: float = 0.8
    throughput_max_ratio: float = 1.0
    deg_cost_per_mwh: float = 0.0
    bess_market_role: str = "behind_meter"  # or "independent"
    no_discharge_on_curtail: bool = False

    # Mid-long term
    pos_tol_ratio: float = 0.05
    cpen_long_applies_to_storage: bool = True

    # Demand response
    dr_aggregation: str = "aggregator"  # or "vpp" / "independent"

    # Forecast calibration (backtest noise injection)
    sca_price: float = 0.10
    sca_power: float = 0.05


# ---------------------------------------------------------------------------
# Forecasts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ForecastResult:
    """Canonical forecast output (§15.3)."""

    name: str  # e.g. "p_dayah_pre"
    unit: str  # "元/MWh" or "MWh/刻"
    freq_minutes: int  # 15 for spot; 0 for monthly (use period index)
    issue_time: pd.Timestamp
    point: pd.Series
    lower: pd.Series | None = None
    upper: pd.Series | None = None
    quantile_level: float | None = None


# ---------------------------------------------------------------------------
# Day-ahead
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DayAheadPlan:
    """Day-ahead coupled optimization output (§7.4, §11.4)."""

    p_bc: np.ndarray  # (96,) charge power
    p_bd: np.ndarray  # (96,) discharge power
    p_b: np.ndarray  # (96,) net discharge = p_bd - p_bc
    soc: np.ndarray  # (97,) SOC trajectory
    q_dayah: np.ndarray  # (96,) day-ahead bid quantity
    expected_cost: float
    expected_revenue: float
    constraint_flags: dict[str, list[int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Intraday
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class IntradayAdjustment:
    """Single rolling-window adjustment (§11.5)."""

    p_b_new: np.ndarray
    delta_p_b: np.ndarray
    delta_revenue: float
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IntradayPlan:
    """Rolling intraday schedule (§11.5)."""

    schedule: DayAheadPlan  # reuse structure
    adjustment: IntradayAdjustment


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SettlementReport:
    """Daily settlement and backtest report (§10, §11.7)."""

    c_daily: float
    cpen_dayah: float
    cpen_long: float
    cost_daily: float
    cost_baseline: float
    delta_cost: float
    opportunity_loss_topk: pd.DataFrame  # columns: [t, loss, cause]
    upside_if_oracle: float


# ---------------------------------------------------------------------------
# Mid-long term
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PositionPlan:
    """Mid-long-term position plan (§6.1, §11.2)."""

    alpha_long: float
    alpha_dayah: float
    alpha_real: float
    q_long_monthly: pd.Series  # MWh/month
    price_band: tuple[float, float]
    expected_cost: float
    budget_used: float
    coverage: float  # ∈ [0,1]


@dataclass(slots=True)
class BidLadder:
    """Centralized bidding ladder (§6.2, §11.3)."""

    direction: str  # "buy" | "sell"
    bid_qty: list[float]  # cumulative quantity, length K
    bid_price: list[float]  # segment price
    clear_prob: list[float]  # marginal clearing probability
    expected_cost: float
    expected_revenue: float


@dataclass(slots=True)
class CorridorAdvice:
    """Degraded output when no counterparty data (§11.3, §13.2)."""

    direction: str
    qty_range: tuple[float, float]
    price_range: tuple[float, float]
    reason: str


# ---------------------------------------------------------------------------
# Demand response
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DRDecision:
    """Demand-response participation decision (§9, §11.6)."""

    participate: bool
    response_qty: float  # MWh
    window: tuple[int, int]  # start/end period
    expected_compensation: float
    arbitrage_opportunity_cost: float
    fulfill_risk: str
    reject_reason: str | None = None
