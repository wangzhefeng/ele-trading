"""多资源联合优化的资源级日内滚动与物理 fallback。"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
from typing import Mapping

import numpy as np

from ele_trading.operations.multi_resource import (
    BESSUnit,
    DemandResponseUnit,
    MultiResourceResult,
    RenewableUnit,
    solve_multi_resource,
)
from ele_trading.operations.resource_runtime import ResourceActual
from ele_trading.optimization.solver import SolverResult, SolveStatus


@dataclass(frozen=True, slots=True)
class MultiResourceIntradayPlan:
    """多资源日内结果；前缀冻结，后缀使用资源级实测 SOC。"""

    executed_prefix: Mapping[str, Mapping[str, list[float]]]
    resource_schedules: Mapping[str, Mapping[str, list[float]]]
    initial_soc_mwh: Mapping[str, float]
    solve_result: SolverResult
    fallback_used: bool
    fallback_reason: str | None = None
    grid_import_mwh: np.ndarray | None = None
    dr_schedules: Mapping[str, Mapping[str, list[float]]] = field(default_factory=dict)
    renewable_schedules: Mapping[str, Mapping[str, list[float]]] = field(
        default_factory=dict
    )


def _frozen_prefix(
    previous: MultiResourceResult,
    *,
    units: tuple[BESSUnit, ...],
    executed_count: int,
) -> dict[str, dict[str, list[float]]]:
    prefix: dict[str, dict[str, list[float]]] = {}
    for unit in units:
        try:
            schedule = previous.resource_schedules[unit.name]
        except KeyError as exc:
            raise ValueError(f"previous result is missing {unit.name!r}") from exc
        for key in ("p_charge", "p_discharge", "soc"):
            if len(schedule[key]) < executed_count:
                raise ValueError("previous result does not cover executed prefix")
        prefix[unit.name] = {
            "p_charge": list(schedule["p_charge"][:executed_count]),
            "p_discharge": list(schedule["p_discharge"][:executed_count]),
            "soc": list(schedule["soc"][:executed_count]),
        }
    return prefix


def _clip_resource_fallback(
    *,
    units: tuple[BESSUnit, ...],
    previous: MultiResourceResult,
    executed_count: int,
    initial_soc_mwh: Mapping[str, float],
    horizon: int,
    dt: float,
) -> dict[str, dict[str, list[float]]]:
    schedules: dict[str, dict[str, list[float]]] = {}
    for unit in units:
        previous_schedule = previous.resource_schedules[unit.name]
        charge_values: list[float] = []
        discharge_values: list[float] = []
        soc_values: list[float] = []
        soc = float(initial_soc_mwh[unit.name])
        for offset in range(horizon):
            index = executed_count + offset
            planned_charge = float(previous_schedule["p_charge"][index])
            planned_discharge = float(previous_schedule["p_discharge"][index])
            charge = min(
                max(0.0, planned_charge),
                unit.p_charge_max,
                max(0.0, (unit.soc_max - soc) / (unit.eta_charge * dt)),
            )
            discharge = min(
                max(0.0, planned_discharge),
                unit.p_discharge_max,
                max(0.0, (soc - unit.soc_min) * unit.eta_discharge / dt),
            )
            if charge > 0.0 and discharge > 0.0:
                if charge >= discharge:
                    discharge = 0.0
                else:
                    charge = 0.0
            soc = float(
                np.clip(
                    soc + unit.eta_charge * charge * dt - discharge * dt / unit.eta_discharge,
                    unit.soc_min,
                    unit.soc_max,
                )
            )
            charge_values.append(charge)
            discharge_values.append(discharge)
            soc_values.append(soc)
        schedules[unit.name] = {
            "p_charge": charge_values,
            "p_discharge": discharge_values,
            "soc": soc_values,
        }
    return schedules


def solve_multi_resource_intraday(
    *,
    load_mwh: np.ndarray,
    price: np.ndarray,
    bess_units: tuple[BESSUnit, ...],
    previous_result: MultiResourceResult,
    executed_count: int,
    dt: float,
    actual_soc_mwh: Mapping[str, float] | None = None,
    actuals: Mapping[str, ResourceActual] | None = None,
    dr_units: tuple[DemandResponseUnit, ...] = (),
    renewable_units: tuple[RenewableUnit, ...] = (),
    solver=None,
) -> MultiResourceIntradayPlan:
    """冻结已执行资源计划，按每台 BESS 实测 SOC 重解未执行后缀。"""
    load = np.asarray(load_mwh, dtype=float)
    prices = np.asarray(price, dtype=float)
    if load.ndim != 1 or prices.shape != load.shape or not len(load):
        raise ValueError("load_mwh and price must be aligned non-empty vectors")
    if not np.isfinite(load).all() or not np.isfinite(prices).all():
        raise ValueError("load_mwh and price must be finite")
    if not isinstance(executed_count, int) or executed_count < 0:
        raise ValueError("executed_count must be a non-negative integer")
    if not np.isfinite(dt) or dt <= 0.0:
        raise ValueError("dt must be positive")
    names = {unit.name for unit in bess_units}
    if (actual_soc_mwh is None) == (actuals is None):
        raise ValueError("provide exactly one of actual_soc_mwh or actuals")
    if actuals is not None:
        renewable_names = {unit.name for unit in renewable_units}
        dr_names = {unit.name for unit in dr_units}
        unknown_names = set(actuals) - names - renewable_names - dr_names
        if unknown_names:
            raise ValueError("actuals contain resources outside the portfolio")
        if not names.issubset(actuals):
            raise ValueError("actuals must cover every BESS unit")

        initial_soc: dict[str, float] = {}
        for name, actual in actuals.items():
            if not isinstance(actual, ResourceActual) or actual.resource_id != name:
                raise ValueError("actuals must contain matching ResourceActual entries")
            if actual.quality_flag != "approved":
                raise ValueError("actual resource quality must be approved")
        for name in names:
            actual = actuals[name]
            try:
                soc_values = actual.interval_values["soc_mwh"]
            except KeyError as exc:
                raise ValueError("actual BESS entries require soc_mwh") from exc
            initial_soc[name] = float(soc_values.iloc[-1])
        dr_initial_net_down_mwh: dict[str, float] = {}
        for unit in dr_units:
            actual = actuals.get(unit.name)
            if actual is None:
                dr_initial_net_down_mwh[unit.name] = 0.0
                continue
            try:
                shift_down = actual.interval_values["shift_down_mwh"]
                shift_up = actual.interval_values["shift_up_mwh"]
            except KeyError as exc:
                raise ValueError(
                    "actual DR entries require shift_down_mwh and shift_up_mwh"
                ) from exc
            dr_initial_net_down_mwh[unit.name] = float(shift_down.sum() - shift_up.sum())
        adjusted_renewables: list[RenewableUnit] = []
        for unit in renewable_units:
            actual = actuals.get(unit.name)
            if actual is None:
                adjusted_renewables.append(unit)
                continue
            try:
                available_values = actual.interval_values["available_mw"]
            except KeyError as exc:
                raise ValueError("actual renewable entries require available_mw") from exc
            if len(available_values) != 1:
                raise ValueError("actual renewable available_mw must be a current scalar")
            available_cap = float(available_values.iloc[-1])
            adjusted_renewables.append(
                replace(unit, available_mw=np.minimum(unit.available_mw, available_cap))
            )
        renewable_units = tuple(adjusted_renewables)
    else:
        assert actual_soc_mwh is not None
        if set(actual_soc_mwh) != names:
            raise ValueError("actual_soc_mwh must cover exactly the BESS units")
        initial_soc = {name: float(value) for name, value in actual_soc_mwh.items()}
        dr_initial_net_down_mwh = {}
    adjusted_units: list[BESSUnit] = []
    for unit in bess_units:
        soc = initial_soc[unit.name]
        if not np.isfinite(soc) or not unit.soc_min <= soc <= unit.soc_max:
            raise ValueError("actual SOC must be finite and within resource limits")
        adjusted_units.append(replace(unit, soc0=soc))
    prefix = _frozen_prefix(
        previous_result,
        units=bess_units,
        executed_count=executed_count,
    )
    result = solve_multi_resource(
        load_mwh=load,
        price=prices,
        bess_units=tuple(adjusted_units),
        dr_units=dr_units,
        renewable_units=renewable_units,
        dr_initial_net_down_mwh=dr_initial_net_down_mwh,
        dt=dt,
        solver=solver,
    )
    if result.solve_result.status in {SolveStatus.OPTIMAL, SolveStatus.FEASIBLE}:
        return MultiResourceIntradayPlan(
            executed_prefix=prefix,
            resource_schedules=result.resource_schedules,
            initial_soc_mwh=initial_soc,
            solve_result=result.solve_result,
            fallback_used=False,
            grid_import_mwh=result.grid_import_mwh,
            dr_schedules=result.dr_schedules,
            renewable_schedules=result.renewable_schedules,
        )
    return MultiResourceIntradayPlan(
        executed_prefix=prefix,
        resource_schedules=_clip_resource_fallback(
            units=bess_units,
            previous=previous_result,
            executed_count=executed_count,
            initial_soc_mwh=initial_soc,
            horizon=len(load),
            dt=dt,
        ),
        initial_soc_mwh=initial_soc,
        solve_result=result.solve_result,
        fallback_used=True,
        fallback_reason=(
            f"multi-resource solve failed: {result.solve_result.status.value}"
        ),
    )
