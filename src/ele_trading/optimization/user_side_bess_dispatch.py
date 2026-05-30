from __future__ import annotations

from pulp import (
    LpBinary,
    LpMinimize,
    LpProblem,
    LpStatus,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
    value,
)

from .interfaces import (
    UserSideBESSDispatchInput,
    UserSideBESSDispatchResult,
    UserSideBESSParams,
)


def run_user_side_bess_dispatch(
    dispatch_input: UserSideBESSDispatchInput,
) -> UserSideBESSDispatchResult:
    """求解用户侧储能调度问题。

    模型只考虑负荷预测、购电价格、需量电费和储能物理约束，不考虑风光出力、
    上网售电或站点策略后处理。
    """
    _validate_input(dispatch_input)

    bess = dispatch_input.bess
    T = range(len(dispatch_input.timestamps))
    model = LpProblem("user_side_bess_dispatch", LpMinimize)

    charge = {
        t: LpVariable(f"charge_{t}", lowBound=0, upBound=bess.p_ch_max)
        for t in T
    }
    discharge = {
        t: LpVariable(f"discharge_{t}", lowBound=0, upBound=bess.p_dis_max)
        for t in T
    }
    soc = {
        t: LpVariable(f"soc_{t}", lowBound=bess.soc_min, upBound=bess.soc_max)
        for t in T
    }
    grid_import = {t: LpVariable(f"grid_import_{t}", lowBound=0) for t in T}
    is_charging = {t: LpVariable(f"is_charging_{t}", cat=LpBinary) for t in T}
    is_discharging = {t: LpVariable(f"is_discharging_{t}", cat=LpBinary) for t in T}
    max_grid_import = LpVariable("max_grid_import", lowBound=0)

    for t in T:
        model += is_charging[t] + is_discharging[t] <= 1
        model += charge[t] <= bess.p_ch_max * is_charging[t]
        model += discharge[t] <= bess.p_dis_max * is_discharging[t]
        model += discharge[t] <= dispatch_input.load_forecast[t]

        model += (
            grid_import[t]
            == dispatch_input.load_forecast[t] + charge[t] - discharge[t]
        )
        model += max_grid_import >= grid_import[t]

        if t == 0:
            previous_soc = dispatch_input.initial_soc
        else:
            previous_soc = soc[t - 1]
        model += (
            soc[t]
            == previous_soc
            + bess.eta_ch * charge[t] * dispatch_input.step_hours
            - discharge[t] * dispatch_input.step_hours / bess.eta_dis
    )

    if dispatch_input.terminal_soc_target is not None:
        model += (
            soc[len(dispatch_input.timestamps) - 1]
            == dispatch_input.terminal_soc_target
        )

    energy_cost_expr = lpSum(
        dispatch_input.buy_price[t] * grid_import[t] * dispatch_input.step_hours
        for t in T
    )
    cycle_cost_expr = lpSum(
        dispatch_input.cycle_cost_rate
        * (charge[t] + discharge[t])
        * dispatch_input.step_hours
        for t in T
    )
    demand_cost_expr = dispatch_input.demand_charge_rate * max_grid_import
    model += energy_cost_expr + demand_cost_expr + cycle_cost_expr

    model.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[model.status]
    if status != "Optimal":
        raise RuntimeError(f"user-side bess dispatch failed: {status}")

    charge_values = [_clean(value(charge[t])) for t in T]
    discharge_values = [_clean(value(discharge[t])) for t in T]
    soc_values = [_clean(value(soc[t])) for t in T]
    grid_import_values = [_clean(value(grid_import[t])) for t in T]
    max_grid_value = _clean(value(max_grid_import))
    energy_cost = sum(
        dispatch_input.buy_price[t] * grid_import_values[t] * dispatch_input.step_hours
        for t in T
    )
    demand_cost = dispatch_input.demand_charge_rate * max_grid_value
    cycle_cost = sum(
        dispatch_input.cycle_cost_rate
        * (charge_values[t] + discharge_values[t])
        * dispatch_input.step_hours
        for t in T
    )

    return UserSideBESSDispatchResult(
        charge_power=charge_values,
        discharge_power=discharge_values,
        net_bess_power=[
            _clean(charge_values[t] - discharge_values[t]) for t in T
        ],
        soc=soc_values,
        grid_import=grid_import_values,
        max_grid_import=max_grid_value,
        energy_cost=energy_cost,
        demand_cost=demand_cost,
        total_cost=energy_cost + demand_cost + cycle_cost,
        constraint_violations=_constraint_violations(
            dispatch_input,
            charge_values,
            discharge_values,
            soc_values,
            grid_import_values,
            max_grid_value,
        ),
    )


def _validate_input(dispatch_input: UserSideBESSDispatchInput) -> None:
    length = len(dispatch_input.timestamps)
    if length == 0:
        raise ValueError("dispatch horizon must not be empty")
    if not (
        len(dispatch_input.load_forecast)
        == len(dispatch_input.buy_price)
        == len(dispatch_input.price_type)
        == length
    ):
        raise ValueError(
            "timestamps, load_forecast, buy_price, and price_type "
            "must have the same length"
        )
    if dispatch_input.step_hours <= 0:
        raise ValueError("step_hours must be positive")
    if dispatch_input.demand_charge_rate < 0:
        raise ValueError("demand_charge_rate must be non-negative")
    if dispatch_input.cycle_cost_rate < 0:
        raise ValueError("cycle_cost_rate must be non-negative")
    if any(load < 0 for load in dispatch_input.load_forecast):
        raise ValueError("load_forecast must be non-negative")
    if any(price < 0 for price in dispatch_input.buy_price):
        raise ValueError("buy_price must be non-negative")

    bess = dispatch_input.bess
    if bess.capacity <= 0:
        raise ValueError("bess.capacity must be positive")
    if bess.soc_min < 0 or bess.soc_max > bess.capacity:
        raise ValueError("bess SOC bounds must be within bess capacity")
    if bess.soc_min > bess.soc_max:
        raise ValueError("bess.soc_min must be less than or equal to bess.soc_max")
    if not bess.soc_min <= dispatch_input.initial_soc <= bess.soc_max:
        raise ValueError("initial_soc must be within bess SOC bounds")
    if dispatch_input.terminal_soc_target is not None and not (
        bess.soc_min <= dispatch_input.terminal_soc_target <= bess.soc_max
    ):
        raise ValueError("terminal_soc_target must be within bess SOC bounds")
    if bess.p_ch_max < 0 or bess.p_dis_max < 0:
        raise ValueError("bess power limits must be non-negative")
    if bess.eta_ch <= 0 or bess.eta_dis <= 0:
        raise ValueError("bess efficiencies must be positive")


def _constraint_violations(
    dispatch_input: UserSideBESSDispatchInput,
    charge_values: list[float],
    discharge_values: list[float],
    soc_values: list[float],
    grid_import_values: list[float],
    max_grid_import: float,
) -> dict[str, float]:
    tolerance = 1e-6
    bess = dispatch_input.bess
    violations = {
        "soc_min": max(bess.soc_min - min(soc_values), 0.0),
        "soc_max": max(max(soc_values) - bess.soc_max, 0.0),
        "charge_max": max(max(charge_values) - bess.p_ch_max, 0.0),
        "discharge_max": max(max(discharge_values) - bess.p_dis_max, 0.0),
        "grid_import_min": max(-min(grid_import_values), 0.0),
        "max_grid_import": max(max(grid_import_values) - max_grid_import, 0.0),
        "discharge_load": max(
            max(
                discharge - load
                for discharge, load in zip(
                    discharge_values, dispatch_input.load_forecast
                )
            ),
            0.0,
        ),
    }
    return {
        name: amount for name, amount in violations.items() if amount > tolerance
    }


def _clean(raw_value: float | None) -> float:
    if raw_value is None:
        raise RuntimeError("solver returned an empty variable value")
    if abs(raw_value) < 1e-9:
        return 0.0
    return float(raw_value)
