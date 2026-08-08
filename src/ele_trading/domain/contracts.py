"""领域契约（市场无关）：交易链路各阶段共享的数据结构。

本包为全项目最底层契约层：只允许依赖标准库/pandas，不得 import
``markets``、``positions``、``operations``、``backtest``、``trading``
等上层包（结构守卫测试强制）。

迁移自原 ``trading/contracts.py``（纯移动，定义逐行不变）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, Mapping, cast

import numpy as np
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


class ContractType(StrEnum):
    """中长期合同的显式物理/金融语义。"""

    FINANCIAL_DIFFERENCE = "financial_difference"
    PHYSICAL_DELIVERY = "physical_delivery"
    HYBRID = "hybrid"


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
class MatchedAward:
    """已验证的报价—回执配对，供运行承诺构造使用。"""

    bid: BidSubmission
    receipt: MarketAwardReceipt


def match_award_receipt(
    *,
    receipt: MarketAwardReceipt,
    bid: BidSubmission,
    already_awarded_mwh: float = 0.0,
) -> MatchedAward:
    """校验市场回执与本周期报价的标识、交割区间和累计成交量一致。"""
    if receipt.bid_id != bid.bid_id:
        raise ValueError("award receipt bid_id must match bid")
    if (
        receipt.delivery_start < bid.delivery_start
        or receipt.delivery_end > bid.delivery_end
    ):
        raise ValueError("award receipt delivery window must be within bid window")
    already_awarded = _require_finite_float(
        already_awarded_mwh,
        "already_awarded_mwh",
    )
    if already_awarded < 0.0:
        raise ValueError("already_awarded_mwh must be non-negative")
    if already_awarded + receipt.cleared_quantity_mwh > bid.quantity_mwh:
        raise ValueError("award receipt quantity exceeds bid quantity")
    return MatchedAward(bid=bid, receipt=receipt)


@dataclass(frozen=True, slots=True)
class AwardedCommitment:
    """已成交的能源履约承诺，按调度网格分配为时段能量义务。"""

    award_id: str
    bid_id: str | None
    external_award_reference: str | None
    market: str
    product: str
    direction: str
    required_energy_mwh: pd.Series
    source_version: str
    award_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.award_id, "award_id")
        _require_non_empty(self.market, "market")
        _require_non_empty(self.product, "product")
        _require_non_empty(self.direction, "direction")
        _require_non_empty(self.source_version, "source_version")
        if not isinstance(self.required_energy_mwh, pd.Series):
            raise ValueError("required_energy_mwh must be a pandas Series")
        if not isinstance(self.required_energy_mwh.index, pd.DatetimeIndex):
            raise ValueError("required_energy_mwh index must be a DatetimeIndex")
        if not np.isfinite(self.required_energy_mwh.to_numpy(dtype=float)).all():
            raise ValueError("required_energy_mwh must be finite")
        award_ids = self.award_ids or (self.award_id,)
        if not all(isinstance(item, str) and item.strip() for item in award_ids):
            raise ValueError("award_ids must contain non-empty IDs")
        object.__setattr__(self, "award_ids", award_ids)

    @classmethod
    def from_matched_award(
        cls,
        matched_award: MatchedAward,
        *,
        valid_times: pd.DatetimeIndex,
        dt_hours: float,
    ) -> "AwardedCommitment":
        """将完整对齐的回执能量均分到其覆盖的调度时段。"""
        if not isinstance(matched_award, MatchedAward):
            raise ValueError("matched_award must be a MatchedAward")
        if not isinstance(valid_times, pd.DatetimeIndex) or not len(valid_times):
            raise ValueError("valid_times must be a non-empty DatetimeIndex")
        if valid_times.tz is None:
            raise ValueError("valid_times must be timezone-aware")
        dt = _require_finite_float(dt_hours, "dt_hours")
        if dt <= 0.0:
            raise ValueError("dt_hours must be positive")
        bid = matched_award.bid
        receipt = matched_award.receipt
        if bid.direction not in {"sell", "buy"}:
            raise ValueError("awarded commitment direction must be buy or sell")
        period_end = valid_times + pd.Timedelta(hours=dt)
        covered = (valid_times >= receipt.delivery_start) & (
            period_end <= receipt.delivery_end
        )
        if not covered.any():
            raise ValueError("award receipt delivery window does not cover a schedule period")
        covered_duration = float(covered.sum()) * dt
        receipt_duration = (
            receipt.delivery_end - receipt.delivery_start
        ).total_seconds() / 3600.0
        if not np.isclose(covered_duration, receipt_duration):
            raise ValueError("award receipt delivery window must align with schedule periods")
        required = pd.Series(0.0, index=valid_times, name="required_energy_mwh")
        required.loc[covered] = receipt.cleared_quantity_mwh / int(covered.sum())
        return cls(
            award_id=receipt.award_id,
            bid_id=receipt.bid_id,
            external_award_reference=receipt.external_award_reference,
            market=bid.market,
            product=bid.product,
            direction=bid.direction,
            required_energy_mwh=required,
            source_version=receipt.source_version,
            award_ids=(receipt.award_id,),
        )

    @classmethod
    def aggregate(
        cls,
        commitments: tuple["AwardedCommitment", ...],
    ) -> "AwardedCommitment":
        """聚合同一 Bid 的网格对齐部分成交，保留原始 Award ID。"""
        if not commitments:
            raise ValueError("at least one awarded commitment is required")
        first = commitments[0]
        for commitment in commitments[1:]:
            if (
                commitment.bid_id != first.bid_id
                or commitment.external_award_reference
                != first.external_award_reference
                or commitment.market != first.market
                or commitment.product != first.product
                or commitment.direction != first.direction
                or commitment.source_version != first.source_version
            ):
                raise ValueError("awarded commitments must have matching provenance")
            if not commitment.required_energy_mwh.index.equals(
                first.required_energy_mwh.index
            ):
                raise ValueError("awarded commitments must share a schedule index")
        if len(commitments) == 1:
            return first
        award_ids = tuple(
            award_id for commitment in commitments for award_id in commitment.award_ids
        )
        required = sum(
            (commitment.required_energy_mwh for commitment in commitments),
            start=pd.Series(0.0, index=first.required_energy_mwh.index),
        )
        return cls(
            award_id=f"aggregate:{'|'.join(award_ids)}",
            bid_id=first.bid_id,
            external_award_reference=first.external_award_reference,
            market=first.market,
            product=first.product,
            direction=first.direction,
            required_energy_mwh=required,
            source_version=first.source_version,
            award_ids=award_ids,
        )


@dataclass(frozen=True, slots=True)
class ResourceMetering:
    """资源级实测放电电量；不得以优化计划替代计量输入。"""

    resource_id: str
    observed_at: pd.Timestamp
    interval_discharge_mwh: pd.Series
    source_version: str

    def __post_init__(self) -> None:
        _require_non_empty(self.resource_id, "resource_id")
        _require_non_empty(self.source_version, "source_version")
        observed_at = _require_aware_timestamp(self.observed_at, "observed_at")
        if not isinstance(self.interval_discharge_mwh, pd.Series):
            raise ValueError("interval_discharge_mwh must be a pandas Series")
        if not isinstance(self.interval_discharge_mwh.index, pd.DatetimeIndex):
            raise ValueError("interval_discharge_mwh index must be a DatetimeIndex")
        if self.interval_discharge_mwh.index.tz is None:
            raise ValueError("interval_discharge_mwh index must be timezone-aware")
        values = self.interval_discharge_mwh.to_numpy(dtype=float)
        if not len(values) or not np.isfinite(values).all() or (values < 0.0).any():
            raise ValueError(
                "interval_discharge_mwh must contain finite non-negative values"
            )
        if not self.interval_discharge_mwh.index.is_unique:
            raise ValueError("interval_discharge_mwh index must be unique")
        object.__setattr__(self, "observed_at", observed_at)


@dataclass(frozen=True, slots=True)
class ResourceExecutionDeviation:
    """资源级计划放电与外部实测的可追溯偏差。"""

    resource_id: str
    planned_discharge_mwh: float
    actual_discharge_mwh: float
    shortfall_mwh: float
    plan_version: str
    metering_version: str

    @classmethod
    def from_planned_discharge(
        cls,
        *,
        resource_id: str,
        planned_interval_discharge_mwh: pd.Series,
        metering: ResourceMetering,
        plan_version: str,
    ) -> "ResourceExecutionDeviation":
        _require_non_empty(resource_id, "resource_id")
        _require_non_empty(plan_version, "plan_version")
        if resource_id != metering.resource_id:
            raise ValueError("resource_id must match resource metering")
        if not isinstance(planned_interval_discharge_mwh, pd.Series):
            raise ValueError("planned_interval_discharge_mwh must be a pandas Series")
        if not isinstance(planned_interval_discharge_mwh.index, pd.DatetimeIndex):
            raise ValueError(
                "planned_interval_discharge_mwh index must be a DatetimeIndex"
            )
        if planned_interval_discharge_mwh.index.tz is None:
            raise ValueError(
                "planned_interval_discharge_mwh index must be timezone-aware"
            )
        planned_values = planned_interval_discharge_mwh.to_numpy(dtype=float)
        if (
            not len(planned_values)
            or not np.isfinite(planned_values).all()
            or (planned_values < 0.0).any()
            or not planned_interval_discharge_mwh.index.is_unique
        ):
            raise ValueError(
                "planned_interval_discharge_mwh must be unique finite non-negative"
            )
        if not planned_interval_discharge_mwh.index.isin(
            metering.interval_discharge_mwh.index
        ).all():
            raise ValueError("resource metering must cover every planned interval")
        planned = float(planned_interval_discharge_mwh.sum())
        actual = float(
            metering.interval_discharge_mwh.reindex(
                planned_interval_discharge_mwh.index
            ).sum()
        )
        return cls(
            resource_id=resource_id,
            planned_discharge_mwh=planned,
            actual_discharge_mwh=actual,
            shortfall_mwh=max(0.0, planned - actual),
            plan_version=plan_version,
            metering_version=metering.source_version,
        )


@dataclass(frozen=True, slots=True)
class AwardFulfillment:
    """以资源级实测计算的已成交能源履约与短缺，不含市场罚则。"""

    award_ids: tuple[str, ...]
    resource_id: str
    committed_mwh: float
    delivered_mwh: float
    shortfall_mwh: float
    metering_version: str

    @classmethod
    def from_commitment(
        cls,
        *,
        commitment: AwardedCommitment,
        metering: ResourceMetering,
    ) -> "AwardFulfillment":
        if commitment.product != "energy" or commitment.direction != "sell":
            raise ValueError(
                "award fulfillment currently supports only sell energy commitments"
            )
        required = cast(
            pd.Series,
            commitment.required_energy_mwh.iloc[
                np.flatnonzero(
                    commitment.required_energy_mwh.to_numpy(dtype=float) > 0.0
                )
            ],
        )
        if not required.index.isin(metering.interval_discharge_mwh.index).all():
            raise ValueError("resource metering must cover every award interval")
        committed = float(required.sum())
        delivered = float(metering.interval_discharge_mwh.reindex(required.index).sum())
        return cls(
            award_ids=commitment.award_ids,
            resource_id=metering.resource_id,
            committed_mwh=committed,
            delivered_mwh=delivered,
            shortfall_mwh=max(0.0, committed - delivered),
            metering_version=metering.source_version,
        )


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
    contract_type: ContractType = ContractType.FINANCIAL_DIFFERENCE

    def __post_init__(self) -> None:
        if not isinstance(self.contract_type, ContractType):
            raise ValueError("contract_type must be a ContractType")


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
