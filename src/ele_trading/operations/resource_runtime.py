"""V6-1 统一资源运行契约。

本模块是运行、实际量、履约分配与组合结算的共同上层语义。它不含市场
产品规则和结算公式；这些仍由 markets 的已确认 profile 消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Mapping

import numpy as np
import pandas as pd

from ele_trading.operations.multi_resource import MultiResourceResult


def _require_non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _normalise_series(
    value: pd.Series,
    *,
    field_name: str,
    allow_negative: bool = False,
) -> pd.Series:
    if not isinstance(value, pd.Series):
        raise ValueError(f"{field_name} must be a pandas Series")
    if not isinstance(value.index, pd.DatetimeIndex) or value.index.tz is None:
        raise ValueError(f"{field_name} must use a timezone-aware DatetimeIndex")
    if not len(value) or not value.index.is_unique:
        raise ValueError(f"{field_name} must have a non-empty unique index")
    numeric = value.astype(float)
    if not np.isfinite(numeric.to_numpy()).all() or (
        not allow_negative and (numeric.to_numpy() < 0.0).any()
    ):
        sign = "finite" if allow_negative else "finite non-negative"
        raise ValueError(f"{field_name} must contain {sign} values")
    return numeric


def _normalise_interval_values(
    values: Mapping[str, pd.Series],
    *,
    field_name: str,
) -> dict[str, pd.Series]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError(f"{field_name} must be a non-empty mapping")
    normalized: dict[str, pd.Series] = {}
    index: pd.DatetimeIndex | None = None
    for metric, series in values.items():
        _require_non_empty(metric, f"{field_name} metric")
        normalized_series = _normalise_series(
            series,
            field_name=f"{field_name}[{metric!r}]",
        )
        if index is None:
            index = normalized_series.index
        elif not normalized_series.index.equals(index):
            raise ValueError(f"{field_name} metrics must share an interval index")
        normalized[metric] = normalized_series
    return normalized


@dataclass(frozen=True, slots=True)
class ResourceSchedule:
    """单个资源的计划时段能量与状态；功率已统一换算为时段 MWh。"""

    resource_id: str
    resource_type: str
    interval_values: Mapping[str, pd.Series]
    plan_version: str

    def __post_init__(self) -> None:
        _require_non_empty(self.resource_id, "resource_id")
        _require_non_empty(self.resource_type, "resource_type")
        _require_non_empty(self.plan_version, "plan_version")
        object.__setattr__(
            self,
            "interval_values",
            _normalise_interval_values(
                self.interval_values,
                field_name="interval_values",
            ),
        )

    @property
    def interval_index(self) -> pd.DatetimeIndex:
        return next(iter(self.interval_values.values())).index


@dataclass(frozen=True, slots=True)
class ResourceActual:
    """资源级实测状态；计划对象绝不能代替此输入。"""

    resource_id: str
    observed_at: pd.Timestamp
    interval_values: Mapping[str, pd.Series]
    quality_flag: str
    source_version: str
    revision: str

    def __post_init__(self) -> None:
        _require_non_empty(self.resource_id, "resource_id")
        _require_non_empty(self.quality_flag, "quality_flag")
        _require_non_empty(self.source_version, "source_version")
        _require_non_empty(self.revision, "revision")
        observed_at = pd.Timestamp(self.observed_at)
        if pd.isna(observed_at) or observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(
            self,
            "interval_values",
            _normalise_interval_values(
                self.interval_values,
                field_name="interval_values",
            ),
        )

    @property
    def interval_index(self) -> pd.DatetimeIndex:
        return next(iter(self.interval_values.values())).index


@dataclass(frozen=True, slots=True)
class CommitmentAllocation:
    """单个 Award 的产品、方向与资源级时段能量分配。"""

    award_id: str
    product: str
    direction: str
    resource_id: str
    interval_energy_mwh: pd.Series
    rule_version: str

    def __post_init__(self) -> None:
        for field_name in (
            "award_id",
            "product",
            "direction",
            "resource_id",
            "rule_version",
        ):
            _require_non_empty(getattr(self, field_name), field_name)
        object.__setattr__(
            self,
            "interval_energy_mwh",
            _normalise_series(
                self.interval_energy_mwh,
                field_name="interval_energy_mwh",
            ),
        )


@dataclass(frozen=True, slots=True)
class PortfolioSettlementInput:
    """组合结算的唯一资源级输入，尚不包含任何市场公式。"""

    grid_import_mwh: pd.Series
    resource_delivery_mwh: Mapping[str, pd.Series]

    def __post_init__(self) -> None:
        grid_import = _normalise_series(
            self.grid_import_mwh,
            field_name="grid_import_mwh",
        )
        if not isinstance(self.resource_delivery_mwh, Mapping):
            raise ValueError("resource_delivery_mwh must be a mapping")
        delivery: dict[str, pd.Series] = {}
        for resource_id, value in self.resource_delivery_mwh.items():
            _require_non_empty(resource_id, "resource_delivery_mwh resource_id")
            series = _normalise_series(
                value,
                field_name=f"resource_delivery_mwh[{resource_id!r}]",
            )
            if not series.index.equals(grid_import.index):
                raise ValueError(
                    "resource_delivery_mwh must share the grid import interval index"
                )
            delivery[resource_id] = series
        object.__setattr__(self, "grid_import_mwh", grid_import)
        object.__setattr__(self, "resource_delivery_mwh", delivery)


@dataclass(frozen=True, slots=True)
class RuntimeFallback:
    """资源级 fallback 的有效区间和原因，禁止静默成功。"""

    resource_ids: tuple[str, ...]
    valid_from: pd.Timestamp
    valid_until: pd.Timestamp
    reason: str

    def __post_init__(self) -> None:
        if not self.resource_ids:
            raise ValueError("resource_ids must not be empty")
        for resource_id in self.resource_ids:
            _require_non_empty(resource_id, "resource_ids item")
        valid_from = pd.Timestamp(self.valid_from)
        valid_until = pd.Timestamp(self.valid_until)
        if (
            pd.isna(valid_from)
            or pd.isna(valid_until)
            or valid_from.tzinfo is None
            or valid_until.tzinfo is None
            or valid_until <= valid_from
        ):
            raise ValueError("fallback validity must be an ordered timezone-aware interval")
        _require_non_empty(self.reason, "reason")
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_until", valid_until)


@dataclass(frozen=True, slots=True)
class ResourceOperationalPlan:
    """V6 唯一资源运行计划语义；单 BESS 是资源数为 1 的实例。"""

    plan_version: str
    schedules: Mapping[str, ResourceSchedule]
    settlement_input: PortfolioSettlementInput
    actuals: Mapping[str, ResourceActual] = field(default_factory=dict)
    commitment_allocations: tuple[CommitmentAllocation, ...] = ()
    fallback: RuntimeFallback | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.plan_version, "plan_version")
        if not isinstance(self.schedules, Mapping) or not self.schedules:
            raise ValueError("schedules must be a non-empty mapping")
        schedules = dict(self.schedules)
        schedule_ids = set(schedules)
        for resource_id, schedule in schedules.items():
            _require_non_empty(resource_id, "schedule resource_id")
            if not isinstance(schedule, ResourceSchedule):
                raise ValueError("schedules values must be ResourceSchedule")
            if schedule.resource_id != resource_id:
                raise ValueError("schedule key must match ResourceSchedule.resource_id")
            if schedule.plan_version != self.plan_version:
                raise ValueError("schedule plan_version must match ResourceOperationalPlan")
            if not schedule.interval_index.equals(self.settlement_input.grid_import_mwh.index):
                raise ValueError("schedule interval index must match settlement input")
        if set(self.settlement_input.resource_delivery_mwh) != schedule_ids:
            raise ValueError("settlement delivery resources must match schedules")
        actuals = dict(self.actuals)
        for resource_id, actual in actuals.items():
            if resource_id not in schedule_ids or not isinstance(actual, ResourceActual):
                raise ValueError("actuals must reference scheduled ResourceActual entries")
            if not actual.interval_index.isin(
                self.settlement_input.grid_import_mwh.index
            ).all():
                raise ValueError("actual interval index must be covered by settlement input")
        for allocation in self.commitment_allocations:
            if not isinstance(allocation, CommitmentAllocation):
                raise ValueError("commitment_allocations must contain CommitmentAllocation")
            if allocation.resource_id not in schedule_ids:
                raise ValueError("commitment allocation must reference a scheduled resource")
            if not allocation.interval_energy_mwh.index.isin(
                self.settlement_input.grid_import_mwh.index
            ).all():
                raise ValueError("commitment allocation must be covered by settlement input")
        if self.fallback is not None:
            if not isinstance(self.fallback, RuntimeFallback):
                raise ValueError("fallback must be a RuntimeFallback")
            if not set(self.fallback.resource_ids).issubset(schedule_ids):
                raise ValueError("fallback resources must be scheduled resources")
        object.__setattr__(self, "schedules", schedules)
        object.__setattr__(self, "actuals", actuals)

    def with_actuals(
        self,
        actuals: Mapping[str, ResourceActual],
    ) -> "ResourceOperationalPlan":
        """将已消费的版本化资源实际量写入同一运行计划。"""
        return replace(self, actuals=dict(actuals))

    @classmethod
    def from_multi_resource_result(
        cls,
        *,
        result: MultiResourceResult,
        valid_times: pd.DatetimeIndex,
        dt_hours: float,
        plan_version: str,
    ) -> "ResourceOperationalPlan":
        """适配现有多资源求解结果，统一为时段能量（MWh）计划。"""
        if not isinstance(result, MultiResourceResult):
            raise ValueError("result must be a MultiResourceResult")
        if not isinstance(valid_times, pd.DatetimeIndex) or not len(valid_times):
            raise ValueError("valid_times must be a non-empty DatetimeIndex")
        if valid_times.tz is None or not valid_times.is_unique:
            raise ValueError("valid_times must be unique and timezone-aware")
        if not isfinite(float(dt_hours)) or float(dt_hours) <= 0.0:
            raise ValueError("dt_hours must be finite and positive")
        if result.grid_import_mwh is None:
            raise ValueError("cannot adapt an unsolved multi-resource result")
        dt = float(dt_hours)

        def energy(values: list[float], name: str) -> pd.Series:
            if len(values) != len(valid_times):
                raise ValueError(f"{name} must cover valid_times")
            return pd.Series(np.asarray(values, dtype=float) * dt, index=valid_times)

        schedules: dict[str, ResourceSchedule] = {}
        deliveries: dict[str, pd.Series] = {}
        for resource_id, values in result.resource_schedules.items():
            discharge = energy(values["p_discharge"], f"{resource_id}.p_discharge")
            schedules[resource_id] = ResourceSchedule(
                resource_id=resource_id,
                resource_type="bess",
                interval_values={
                    "charge_mwh": energy(values["p_charge"], f"{resource_id}.p_charge"),
                    "discharge_mwh": discharge,
                    "soc_mwh": pd.Series(values["soc"], index=valid_times, dtype=float),
                },
                plan_version=plan_version,
            )
            deliveries[resource_id] = discharge
        for resource_id, values in result.dr_schedules.items():
            shift_down = energy(values["shift_down_mw"], f"{resource_id}.shift_down")
            schedules[resource_id] = ResourceSchedule(
                resource_id=resource_id,
                resource_type="demand_response",
                interval_values={
                    "shift_up_mwh": energy(values["shift_up_mw"], f"{resource_id}.shift_up"),
                    "shift_down_mwh": shift_down,
                },
                plan_version=plan_version,
            )
            deliveries[resource_id] = shift_down
        for resource_id, values in result.renewable_schedules.items():
            used = energy(values["used_mw"], f"{resource_id}.used")
            schedules[resource_id] = ResourceSchedule(
                resource_id=resource_id,
                resource_type="renewable",
                interval_values={
                    "used_mwh": used,
                    "curtailed_mwh": energy(
                        values["curtailed_mw"],
                        f"{resource_id}.curtailed",
                    ),
                },
                plan_version=plan_version,
            )
            deliveries[resource_id] = used
        grid_import = np.asarray(result.grid_import_mwh, dtype=float)
        if grid_import.shape != (len(valid_times),):
            raise ValueError("grid_import_mwh must cover valid_times")
        return cls(
            plan_version=plan_version,
            schedules=schedules,
            settlement_input=PortfolioSettlementInput(
                grid_import_mwh=pd.Series(grid_import, index=valid_times),
                resource_delivery_mwh=deliveries,
            ),
        )
