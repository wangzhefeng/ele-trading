"""领域契约（市场无关）：交易链路各阶段共享的数据结构。

本包为全项目最底层契约层：只允许依赖标准库/pandas，不得 import
``markets``、``positions``、``operations``、``backtest``、``trading``
等上层包（结构守卫测试强制）。

迁移自原 ``trading/contracts.py``（纯移动，定义逐行不变）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Mapping, cast

import pandas as pd


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_aware_timestamp(value: Any, field_name: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field_name} must be a timezone-aware timestamp")
    return cast(pd.Timestamp, timestamp)


def _require_finite_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite") from exc
    if not isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class BidSubmission:
    """市场无关的单一能源报价及其版本化决策证据。"""

    bid_id: str
    market: str
    product: str
    direction: str
    issue_time: pd.Timestamp
    delivery_start: pd.Timestamp
    delivery_end: pd.Timestamp
    quantity_mwh: float
    price_cny_per_mwh: float
    forecast_version: str
    rule_version: str
    resource_version: str
    strategy_version: str
    config_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "bid_id",
            "market",
            "product",
            "direction",
            "forecast_version",
            "rule_version",
            "resource_version",
            "strategy_version",
            "config_version",
        ):
            _require_non_empty(getattr(self, field_name), field_name)
        issue_time = _require_aware_timestamp(self.issue_time, "issue_time")
        delivery_start = _require_aware_timestamp(
            self.delivery_start,
            "delivery_start",
        )
        delivery_end = _require_aware_timestamp(
            self.delivery_end,
            "delivery_end",
        )
        if delivery_end <= delivery_start:
            raise ValueError("delivery_end must be later than delivery_start")
        if delivery_start < issue_time:
            raise ValueError("delivery_start cannot be earlier than issue_time")
        quantity_mwh = _require_finite_float(self.quantity_mwh, "quantity_mwh")
        if quantity_mwh <= 0.0:
            raise ValueError("quantity_mwh must be positive")
        price_cny_per_mwh = _require_finite_float(
            self.price_cny_per_mwh,
            "price_cny_per_mwh",
        )
        object.__setattr__(self, "issue_time", issue_time)
        object.__setattr__(self, "delivery_start", delivery_start)
        object.__setattr__(self, "delivery_end", delivery_end)
        object.__setattr__(self, "quantity_mwh", quantity_mwh)
        object.__setattr__(self, "price_cny_per_mwh", price_cny_per_mwh)


@dataclass(frozen=True, slots=True)
class MarketAwardReceipt:
    """市场回执：出清机构返回的成交证据（AwardEvent 的唯一构造依据）。"""

    award_id: str
    receipt_time: pd.Timestamp
    delivery_start: pd.Timestamp
    delivery_end: pd.Timestamp
    cleared_quantity_mwh: float
    cleared_price_cny_per_mwh: float
    source_version: str
    bid_id: str | None = None
    external_award_reference: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.award_id, "award_id")
        _require_non_empty(self.source_version, "source_version")
        if (self.bid_id is None) == (self.external_award_reference is None):
            raise ValueError(
                "exactly one of bid_id or external_award_reference is required"
            )
        if self.bid_id is not None:
            _require_non_empty(self.bid_id, "bid_id")
        if self.external_award_reference is not None:
            _require_non_empty(
                self.external_award_reference,
                "external_award_reference",
            )
        receipt_time = _require_aware_timestamp(self.receipt_time, "receipt_time")
        delivery_start = _require_aware_timestamp(
            self.delivery_start,
            "delivery_start",
        )
        delivery_end = _require_aware_timestamp(self.delivery_end, "delivery_end")
        if delivery_end <= delivery_start:
            raise ValueError("delivery_end must be later than delivery_start")
        cleared_quantity = _require_finite_float(
            self.cleared_quantity_mwh,
            "cleared_quantity_mwh",
        )
        if cleared_quantity <= 0.0:
            raise ValueError("cleared_quantity_mwh must be positive")
        cleared_price = _require_finite_float(
            self.cleared_price_cny_per_mwh,
            "cleared_price_cny_per_mwh",
        )
        object.__setattr__(self, "receipt_time", receipt_time)
        object.__setattr__(self, "delivery_start", delivery_start)
        object.__setattr__(self, "delivery_end", delivery_end)
        object.__setattr__(self, "cleared_quantity_mwh", cleared_quantity)
        object.__setattr__(self, "cleared_price_cny_per_mwh", cleared_price)


@dataclass(frozen=True, slots=True)
class BillingStatement:
    """正式账单：结算机构发布的分项金额及其确认状态。"""

    statement_version: str
    lines: Mapping[str, float]
    confirmed: bool
    tolerance: float = 1e-6

    def __post_init__(self) -> None:
        _require_non_empty(self.statement_version, "statement_version")
        if not isinstance(self.lines, Mapping) or not self.lines:
            raise ValueError("lines must be a non-empty mapping")
        normalized: dict[str, float] = {}
        for name, amount in self.lines.items():
            _require_non_empty(name, "lines line item")
            normalized[name] = _require_finite_float(
                amount,
                f"lines[{name!r}]",
            )
        tolerance = _require_finite_float(self.tolerance, "tolerance")
        if tolerance < 0.0:
            raise ValueError("tolerance must be non-negative")
        object.__setattr__(self, "lines", normalized)
        object.__setattr__(self, "confirmed", bool(self.confirmed))
        object.__setattr__(self, "tolerance", tolerance)


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
    objective_components: dict[str, float] = field(default_factory=dict)
    active_constraints: Mapping[str, tuple[int, ...]] = field(
        default_factory=dict
    )
    diagnostics: Mapping[str, str] = field(default_factory=dict)
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
    price_forecasts: Mapping[str, Any] = field(default_factory=dict)
    market_state_forecast: Any | None = None

    def __post_init__(self) -> None:
        issue_time = pd.Timestamp(self.issue_time)
        if pd.isna(issue_time) or issue_time.tzinfo is None:
            raise ValueError("issue_time must be a timezone-aware timestamp")
        price_forecasts = dict(self.price_forecasts)
        if not price_forecasts:
            price_forecasts["real_time_settlement"] = self.price_forecast
        if not any(
            value is self.price_forecast
            for value in price_forecasts.values()
        ):
            raise ValueError(
                "price_forecast must be one of price_forecasts values"
            )
        self.issue_time = cast(pd.Timestamp, issue_time)
        self.price_forecasts = price_forecasts

    def get_price_forecast(self, price_role: str) -> Any:
        try:
            return self.price_forecasts[price_role]
        except KeyError as exc:
            raise KeyError(
                f"price forecast role {price_role!r} is unavailable"
            ) from exc


@dataclass(slots=True)
class DRCommitment:
    """DR 联合优化产出的申报承诺（两阶段求解结果）。"""

    committed_qty: float           # 申报增量放电能量（MWh），0 表示不参与
    window: tuple[int, int]        # DR 窗口 [start, end)
    baseline_qty: float            # 基线放电能量 Q0（MWh）
    expected_compensation: float   # 预期补偿（元）
    expected_incremental: float    # 预期增量放电（MWh）
    participate: bool              # 是否参与
    reject_reason: str | None = None


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
    dr_commitment: DRCommitment | None = None


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
