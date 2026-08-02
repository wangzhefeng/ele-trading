"""领域契约（市场无关）：交易链路各阶段共享的数据结构。

本包为全项目最底层契约层：只允许依赖标准库/pandas，不得 import
``markets``、``positions``、``operations``、``backtest``、``trading``
等上层包（结构守卫测试强制）。

迁移自原 ``trading/contracts.py``（纯移动，定义逐行不变）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, cast

import pandas as pd


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
