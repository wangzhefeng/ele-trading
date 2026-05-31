from __future__ import annotations

from ele_trading.utils import clean_value
from .interfaces import (
    UserSidePVExportParams,
    UserSideRenewableDispatchInput,
    UserSideRenewableDispatchResult,
)


def run_user_side_renewable_dispatch(
    dispatch_input: UserSideRenewableDispatchInput,
) -> UserSideRenewableDispatchResult:
    """Run deterministic user-side renewable dispatch without storage."""
    _validate_input(dispatch_input)

    renewable_to_load = []
    renewable_to_grid = []
    renewable_curtailment = []
    grid_import = []

    for load, renewable in zip(
        dispatch_input.load_forecast,
        dispatch_input.renewable_forecast,
    ):
        local_renewable = min(load, renewable)
        surplus = max(renewable - local_renewable, 0.0)
        export_power = _export_power(surplus, dispatch_input.export)
        curtailment = surplus - export_power

        renewable_to_load.append(clean_value(local_renewable))
        renewable_to_grid.append(clean_value(export_power))
        renewable_curtailment.append(clean_value(curtailment))
        grid_import.append(clean_value(max(load - local_renewable, 0.0)))

    max_grid_import = clean_value(max(grid_import))
    energy_cost = sum(
        dispatch_input.buy_price[t] * grid_import[t] * dispatch_input.step_hours
        for t in range(len(dispatch_input.timestamps))
    )
    demand_cost = dispatch_input.demand_charge_rate * max_grid_import
    sell_revenue = sum(
        dispatch_input.export.sell_price * renewable_to_grid[t] * dispatch_input.step_hours
        for t in range(len(dispatch_input.timestamps))
    )
    curtailment_cost = sum(
        dispatch_input.export.curtailment_cost_rate
        * renewable_curtailment[t]
        * dispatch_input.step_hours
        for t in range(len(dispatch_input.timestamps))
    )

    return UserSideRenewableDispatchResult(
        renewable_to_load=renewable_to_load,
        renewable_to_grid=renewable_to_grid,
        renewable_curtailment=renewable_curtailment,
        grid_import=grid_import,
        max_grid_import=max_grid_import,
        energy_cost=energy_cost,
        demand_cost=demand_cost,
        sell_revenue=sell_revenue,
        curtailment_cost=curtailment_cost,
        total_cost=energy_cost + demand_cost + curtailment_cost - sell_revenue,
        constraint_violations=_constraint_violations(
            dispatch_input,
            renewable_to_load,
            renewable_to_grid,
            renewable_curtailment,
            grid_import,
            max_grid_import,
        ),
    )


def _validate_input(dispatch_input: UserSideRenewableDispatchInput) -> None:
    length = len(dispatch_input.timestamps)
    if length == 0:
        raise ValueError("dispatch horizon must not be empty")
    if not (
        len(dispatch_input.load_forecast)
        == len(dispatch_input.renewable_forecast)
        == len(dispatch_input.buy_price)
        == len(dispatch_input.price_type)
        == length
    ):
        raise ValueError(
            "timestamps, load_forecast, renewable_forecast, buy_price, "
            "and price_type must have the same length"
        )
    if dispatch_input.step_hours <= 0:
        raise ValueError("step_hours must be positive")
    if dispatch_input.demand_charge_rate < 0:
        raise ValueError("demand_charge_rate must be non-negative")
    if any(load < 0 for load in dispatch_input.load_forecast):
        raise ValueError("load_forecast must be non-negative")
    if any(renewable < 0 for renewable in dispatch_input.renewable_forecast):
        raise ValueError("renewable_forecast must be non-negative")
    if any(price < 0 for price in dispatch_input.buy_price):
        raise ValueError("buy_price must be non-negative")
    export = dispatch_input.export
    if export.sell_price < 0:
        raise ValueError("export.sell_price must be non-negative")
    if export.curtailment_cost_rate < 0:
        raise ValueError("export.curtailment_cost_rate must be non-negative")
    if export.export_limit is not None and export.export_limit < 0:
        raise ValueError("export.export_limit must be non-negative")


def _export_power(surplus: float, export: UserSidePVExportParams) -> float:
    if not export.allow_export:
        return 0.0
    if export.export_limit is None:
        return surplus
    return min(surplus, export.export_limit)


def _constraint_violations(
    dispatch_input: UserSideRenewableDispatchInput,
    renewable_to_load: list[float],
    renewable_to_grid: list[float],
    renewable_curtailment: list[float],
    grid_import: list[float],
    max_grid_import: float,
) -> dict[str, float]:
    tolerance = 1e-6
    violations = {
        "renewable_balance": max(
            abs(
                renewable_to_load[t]
                + renewable_to_grid[t]
                + renewable_curtailment[t]
                - renewable
            )
            for t, renewable in enumerate(dispatch_input.renewable_forecast)
        ),
        "load_balance": max(
            abs(renewable_to_load[t] + grid_import[t] - load)
            for t, load in enumerate(dispatch_input.load_forecast)
        ),
        "grid_import_min": max(-min(grid_import), 0.0),
        "max_grid_import": max(max(grid_import) - max_grid_import, 0.0),
    }
    return {
        name: amount for name, amount in violations.items() if amount > tolerance
    }
