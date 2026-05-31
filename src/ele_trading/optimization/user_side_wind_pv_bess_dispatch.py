from __future__ import annotations

from ele_trading.utils import clean_value

from .interfaces import (
    UserSideRenewableBESSDispatchInput,
    UserSideWindPVBESSDispatchInput,
    UserSideWindPVBESSDispatchResult,
)
from .user_side_renewable_bess_dispatch import run_user_side_renewable_bess_dispatch


def run_user_side_wind_pv_bess_dispatch(
    dispatch_input: UserSideWindPVBESSDispatchInput,
) -> UserSideWindPVBESSDispatchResult:
    """Run user-side wind + PV + BESS dispatch as a MILP."""
    _validate_input(dispatch_input)
    renewable_forecast = [
        clean_value(pv + wind)
        for pv, wind in zip(dispatch_input.pv_forecast, dispatch_input.wind_forecast)
    ]
    renewable_result = run_user_side_renewable_bess_dispatch(
        UserSideRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=renewable_forecast,
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
    return UserSideWindPVBESSDispatchResult(
        pv_forecast=dispatch_input.pv_forecast,
        wind_forecast=dispatch_input.wind_forecast,
        renewable_forecast=renewable_forecast,
        renewable_to_load=renewable_result.renewable_to_load,
        renewable_to_bess=renewable_result.renewable_to_bess,
        renewable_to_grid=renewable_result.renewable_to_grid,
        renewable_curtailment=renewable_result.renewable_curtailment,
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
        constraint_violations=renewable_result.constraint_violations,
    )


def _validate_input(dispatch_input: UserSideWindPVBESSDispatchInput) -> None:
    length = len(dispatch_input.timestamps)
    if not (
        len(dispatch_input.pv_forecast)
        == len(dispatch_input.wind_forecast)
        == length
    ):
        raise ValueError("timestamps, pv_forecast, and wind_forecast must have the same length")
    if any(pv < 0 for pv in dispatch_input.pv_forecast):
        raise ValueError("pv_forecast must be non-negative")
    if any(wind < 0 for wind in dispatch_input.wind_forecast):
        raise ValueError("wind_forecast must be non-negative")
