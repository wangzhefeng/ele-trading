from __future__ import annotations

from .interfaces import (
    UserSideRenewableDispatchInput,
    UserSideWindDispatchInput,
    UserSideWindDispatchResult,
)
from .user_side_renewable_dispatch_class import run_user_side_renewable_dispatch


def run_user_side_wind_dispatch(dispatch_input: UserSideWindDispatchInput) -> UserSideWindDispatchResult:
    """
    Run deterministic user-side wind dispatch without storage.
    """
    renewable_result = run_user_side_renewable_dispatch(
        UserSideRenewableDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=dispatch_input.wind_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
        )
    )
    
    return UserSideWindDispatchResult(
        wind_to_load=renewable_result.renewable_to_load,
        wind_to_grid=renewable_result.renewable_to_grid,
        wind_curtailment=renewable_result.renewable_curtailment,
        grid_import=renewable_result.grid_import,
        max_grid_import=renewable_result.max_grid_import,
        energy_cost=renewable_result.energy_cost,
        demand_cost=renewable_result.demand_cost,
        sell_revenue=renewable_result.sell_revenue,
        curtailment_cost=renewable_result.curtailment_cost,
        total_cost=renewable_result.total_cost,
        constraint_violations=renewable_result.constraint_violations,
    )
