from __future__ import annotations

from pulp import (
    LpBinary,
    LpMinimize,
    LpProblem,
    LpVariable,
    PULP_CBC_CMD,
    lpSum,
    value,
)

from ele_trading.utils import check_pulp_status, clean_value, extract_timestamp_hours
from .interfaces import (
    UserSideDispatchPolicy,
    UserSidePVExportParams,
    UserSidePVBESSDispatchInput,
    UserSidePVBESSDispatchResult,
    UserSideBESSParams,
)


def run_user_side_pv_bess_dispatch(
    dispatch_input: UserSidePVBESSDispatchInput,
) -> UserSidePVBESSDispatchResult:
    """Run user-side PV + bess dispatch as a MILP."""
    _validate_input(dispatch_input)

    bess = dispatch_input.bess
    T = range(len(dispatch_input.timestamps))
    model = LpProblem("user_side_pv_bess_dispatch", LpMinimize)

    pv_to_load = {t: LpVariable(f"pv_to_load_{t}", lowBound=0) for t in T}
    pv_to_bess = {t: LpVariable(f"pv_to_bess_{t}", lowBound=0) for t in T}
    pv_to_grid = {t: LpVariable(f"pv_to_grid_{t}", lowBound=0) for t in T}
    pv_curtailment = {t: LpVariable(f"pv_curtailment_{t}", lowBound=0) for t in T}
    grid_to_load = {t: LpVariable(f"grid_to_load_{t}", lowBound=0) for t in T}
    grid_to_bess = {t: LpVariable(f"grid_to_bess_{t}", lowBound=0) for t in T}
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

    timestamp_hours = extract_timestamp_hours(dispatch_input.timestamps)
    for t in T:
        model += (
            pv_to_load[t]
            + pv_to_bess[t]
            + pv_to_grid[t]
            + pv_curtailment[t]
            == dispatch_input.pv_forecast[t]
        )
        model += (
            pv_to_load[t] + discharge[t] + grid_to_load[t]
            == dispatch_input.load_forecast[t]
        )
        model += charge[t] == pv_to_bess[t] + grid_to_bess[t]
        model += grid_import[t] == grid_to_load[t] + grid_to_bess[t]
        model += max_grid_import >= grid_import[t]
        model += is_charging[t] + is_discharging[t] <= 1
        model += charge[t] <= bess.p_ch_max * is_charging[t]
        model += discharge[t] <= bess.p_dis_max * is_discharging[t]
        model += discharge[t] <= dispatch_input.load_forecast[t]

        if not dispatch_input.export.allow_export:
            model += pv_to_grid[t] == 0
        if dispatch_input.export.export_limit is not None:
            model += pv_to_grid[t] <= dispatch_input.export.export_limit

        policy = dispatch_input.policy
        if policy is not None:
            hour = timestamp_hours[t]
            if policy.charge_allowed_hours is not None and hour not in policy.charge_allowed_hours:
                model += pv_to_bess[t] == 0
                model += grid_to_bess[t] == 0
            if policy.discharge_allowed_hours is not None and hour not in policy.discharge_allowed_hours:
                model += discharge[t] == 0

        previous_soc = dispatch_input.initial_soc if t == 0 else soc[t - 1]
        model += (
            soc[t]
            == previous_soc
            + bess.eta_ch * charge[t] * dispatch_input.step_hours
            - discharge[t] * dispatch_input.step_hours / bess.eta_dis
        )

    if dispatch_input.terminal_soc_target is not None:
        model += soc[len(dispatch_input.timestamps) - 1] == dispatch_input.terminal_soc_target

    energy_cost_expr = lpSum(
        dispatch_input.buy_price[t] * grid_import[t] * dispatch_input.step_hours
        for t in T
    )
    demand_cost_expr = dispatch_input.demand_charge_rate * max_grid_import
    sell_revenue_expr = lpSum(
        dispatch_input.export.sell_price * pv_to_grid[t] * dispatch_input.step_hours
        for t in T
    )
    curtailment_cost_expr = lpSum(
        dispatch_input.export.curtailment_cost_rate
        * pv_curtailment[t]
        * dispatch_input.step_hours
        for t in T
    )
    cycle_cost_expr = lpSum(
        dispatch_input.cycle_cost_rate
        * (charge[t] + discharge[t])
        * dispatch_input.step_hours
        for t in T
    )
    policy_expr = _policy_objective(dispatch_input, pv_to_load, pv_to_bess, pv_to_grid)
    model += (
        energy_cost_expr
        + demand_cost_expr
        + curtailment_cost_expr
        + cycle_cost_expr
        - sell_revenue_expr
        + policy_expr
    )

    model.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(model, "user-side PV bess dispatch")

    pv_to_load_values = [clean_value(value(pv_to_load[t])) for t in T]
    pv_to_bess_values = [clean_value(value(pv_to_bess[t])) for t in T]
    pv_to_grid_values = [clean_value(value(pv_to_grid[t])) for t in T]
    pv_curtailment_values = [clean_value(value(pv_curtailment[t])) for t in T]
    grid_to_load_values = [clean_value(value(grid_to_load[t])) for t in T]
    grid_to_bess_values = [clean_value(value(grid_to_bess[t])) for t in T]
    charge_values = [clean_value(value(charge[t])) for t in T]
    discharge_values = [clean_value(value(discharge[t])) for t in T]
    soc_values = [clean_value(value(soc[t])) for t in T]
    grid_import_values = [clean_value(value(grid_import[t])) for t in T]
    max_grid_value = clean_value(value(max_grid_import))

    energy_cost = sum(
        dispatch_input.buy_price[t] * grid_import_values[t] * dispatch_input.step_hours
        for t in T
    )
    demand_cost = dispatch_input.demand_charge_rate * max_grid_value
    sell_revenue = sum(
        dispatch_input.export.sell_price
        * pv_to_grid_values[t]
        * dispatch_input.step_hours
        for t in T
    )
    curtailment_cost = sum(
        dispatch_input.export.curtailment_cost_rate
        * pv_curtailment_values[t]
        * dispatch_input.step_hours
        for t in T
    )
    cycle_cost = sum(
        dispatch_input.cycle_cost_rate
        * (charge_values[t] + discharge_values[t])
        * dispatch_input.step_hours
        for t in T
    )

    return UserSidePVBESSDispatchResult(
        pv_to_load=pv_to_load_values,
        pv_to_bess=pv_to_bess_values,
        pv_to_grid=pv_to_grid_values,
        pv_curtailment=pv_curtailment_values,
        grid_to_load=grid_to_load_values,
        grid_to_bess=grid_to_bess_values,
        charge_power=charge_values,
        discharge_power=discharge_values,
        net_bess_power=[
            clean_value(charge_values[t] - discharge_values[t]) for t in T
        ],
        soc=soc_values,
        grid_import=grid_import_values,
        max_grid_import=max_grid_value,
        energy_cost=energy_cost,
        demand_cost=demand_cost,
        sell_revenue=sell_revenue,
        curtailment_cost=curtailment_cost,
        cycle_cost=cycle_cost,
        total_cost=energy_cost + demand_cost + curtailment_cost + cycle_cost - sell_revenue,
        constraint_violations=_constraint_violations(
            dispatch_input,
            pv_to_load_values,
            pv_to_bess_values,
            pv_to_grid_values,
            pv_curtailment_values,
            grid_to_load_values,
            grid_to_bess_values,
            charge_values,
            discharge_values,
            soc_values,
            grid_import_values,
            max_grid_value,
        ),
    )


def _validate_input(dispatch_input: UserSidePVBESSDispatchInput) -> None:
    length = len(dispatch_input.timestamps)
    if length == 0:
        raise ValueError("dispatch horizon must not be empty")
    if not (
        len(dispatch_input.load_forecast)
        == len(dispatch_input.pv_forecast)
        == len(dispatch_input.buy_price)
        == len(dispatch_input.price_type)
        == length
    ):
        raise ValueError(
            "timestamps, load_forecast, pv_forecast, buy_price, and price_type "
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
    if any(pv < 0 for pv in dispatch_input.pv_forecast):
        raise ValueError("pv_forecast must be non-negative")
    if any(price < 0 for price in dispatch_input.buy_price):
        raise ValueError("buy_price must be non-negative")

    export = dispatch_input.export
    if export.sell_price < 0:
        raise ValueError("export.sell_price must be non-negative")
    if export.curtailment_cost_rate < 0:
        raise ValueError("export.curtailment_cost_rate must be non-negative")
    if export.export_limit is not None and export.export_limit < 0:
        raise ValueError("export.export_limit must be non-negative")

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
    _validate_policy(dispatch_input.policy)


def _validate_policy(policy: UserSideDispatchPolicy | None) -> None:
    if policy is None:
        return
    for name, hours in (
        ("charge_allowed_hours", policy.charge_allowed_hours),
        ("discharge_allowed_hours", policy.discharge_allowed_hours),
    ):
        if hours is None:
            continue
        if any(hour < 0 or hour > 23 for hour in hours):
            raise ValueError(f"policy.{name} must contain hours in [0, 23]")
    if policy.pv_to_bess_reward_rate < 0:
        raise ValueError("policy.pv_to_bess_reward_rate must be non-negative")
    if policy.pv_to_load_reward_rate < 0:
        raise ValueError("policy.pv_to_load_reward_rate must be non-negative")
    if policy.pv_export_penalty_rate < 0:
        raise ValueError("policy.pv_export_penalty_rate must be non-negative")


def _policy_objective(
    dispatch_input: UserSidePVBESSDispatchInput,
    pv_to_load,
    pv_to_bess,
    pv_to_grid,
):
    policy = dispatch_input.policy
    if policy is None:
        return 0.0
    T = range(len(dispatch_input.timestamps))
    return (
        -policy.pv_to_bess_reward_rate
        * lpSum(pv_to_bess[t] * dispatch_input.step_hours for t in T)
        - policy.pv_to_load_reward_rate
        * lpSum(pv_to_load[t] * dispatch_input.step_hours for t in T)
        + policy.pv_export_penalty_rate
        * lpSum(pv_to_grid[t] * dispatch_input.step_hours for t in T)
    )


def _constraint_violations(
    dispatch_input: UserSidePVBESSDispatchInput,
    pv_to_load: list[float],
    pv_to_bess: list[float],
    pv_to_grid: list[float],
    pv_curtailment: list[float],
    grid_to_load: list[float],
    grid_to_bess: list[float],
    charge_values: list[float],
    discharge_values: list[float],
    soc_values: list[float],
    grid_import_values: list[float],
    max_grid_import: float,
) -> dict[str, float]:
    tolerance = 1e-6
    bess = dispatch_input.bess
    violations = {
        "pv_balance": max(
            abs(
                pv_to_load[t]
                + pv_to_bess[t]
                + pv_to_grid[t]
                + pv_curtailment[t]
                - dispatch_input.pv_forecast[t]
            )
            for t in range(len(dispatch_input.timestamps))
        ),
        "load_balance": max(
            abs(
                pv_to_load[t]
                + discharge_values[t]
                + grid_to_load[t]
                - dispatch_input.load_forecast[t]
            )
            for t in range(len(dispatch_input.timestamps))
        ),
        "grid_import": max(
            abs(grid_import_values[t] - grid_to_load[t] - grid_to_bess[t])
            for t in range(len(dispatch_input.timestamps))
        ),
        "charge_balance": max(
            abs(charge_values[t] - pv_to_bess[t] - grid_to_bess[t])
            for t in range(len(dispatch_input.timestamps))
        ),
        "soc_min": max(bess.soc_min - min(soc_values), 0.0),
        "soc_max": max(max(soc_values) - bess.soc_max, 0.0),
        "charge_max": max(max(charge_values) - bess.p_ch_max, 0.0),
        "discharge_max": max(max(discharge_values) - bess.p_dis_max, 0.0),
        "grid_import_min": max(-min(grid_import_values), 0.0),
        "max_grid_import": max(max(grid_import_values) - max_grid_import, 0.0),
    }
    return {
        name: amount for name, amount in violations.items() if amount > tolerance
    }
