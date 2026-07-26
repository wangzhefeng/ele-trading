from __future__ import annotations

from .interfaces import (
    UserSideBESSParams,
    UserSideDispatchPolicy,
    UserSidePVBESSDispatchInput,
    UserSidePVBESSDispatchResult,
    UserSidePVExportParams,
    UserSideRenewableBESSDispatchInput,
)
from .user_side_renewable_bess_dispatch_class import run_user_side_renewable_bess_dispatch


def run_user_side_pv_bess_dispatch(dispatch_input: UserSidePVBESSDispatchInput) -> UserSidePVBESSDispatchResult:
    """Run user-side PV + bess dispatch as a MILP."""
    _validate_input(dispatch_input)
    renewable_result = run_user_side_renewable_bess_dispatch(
        UserSideRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=dispatch_input.pv_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
            bess=dispatch_input.bess,
            initial_soc=dispatch_input.initial_soc,
            terminal_soc_target=dispatch_input.terminal_soc_target,
            cycle_cost_rate=dispatch_input.cycle_cost_rate,
            policy=dispatch_input.policy,
        )
    )
    return UserSidePVBESSDispatchResult(
        pv_to_load=renewable_result.renewable_to_load,
        pv_to_bess=renewable_result.renewable_to_bess,
        pv_to_grid=renewable_result.renewable_to_grid,
        pv_curtailment=renewable_result.renewable_curtailment,
        grid_to_load=renewable_result.grid_to_load,
        grid_to_bess=renewable_result.grid_to_bess,
        charge_power=renewable_result.charge_power,
        discharge_power=renewable_result.discharge_power,
        net_bess_power=renewable_result.net_bess_power,
        soc=renewable_result.soc,
        grid_import=renewable_result.grid_import,
        max_grid_import=renewable_result.max_grid_import,
        energy_cost=renewable_result.energy_cost,
        demand_cost=renewable_result.demand_cost,
        sell_revenue=renewable_result.sell_revenue,
        curtailment_cost=renewable_result.curtailment_cost,
        cycle_cost=renewable_result.cycle_cost,
        total_cost=renewable_result.total_cost,
        constraint_violations=_pv_constraint_violations(
            renewable_result.constraint_violations
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
    export = dispatch_input.export
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


def _pv_constraint_violations(violations: dict[str, float]) -> dict[str, float]:
    return {
        ("pv_balance" if name == "renewable_balance" else name): amount
        for name, amount in violations.items()
    }
