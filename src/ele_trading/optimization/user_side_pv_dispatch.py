from __future__ import annotations

from .interfaces import (
    UserSidePVDispatchInput,
    UserSidePVDispatchResult,
    UserSidePVExportParams,
)


def run_user_side_pv_dispatch(
    dispatch_input: UserSidePVDispatchInput,
) -> UserSidePVDispatchResult:
    """Run deterministic user-side PV dispatch without storage."""
    _validate_input(dispatch_input)

    pv_to_load = []
    pv_to_grid = []
    pv_curtailment = []
    grid_import = []

    for load, pv in zip(dispatch_input.load_forecast, dispatch_input.pv_forecast):
        local_pv = min(load, pv)
        surplus = max(pv - local_pv, 0.0)
        export_power = _export_power(surplus, dispatch_input.export)
        curtailment = surplus - export_power

        pv_to_load.append(_clean(local_pv))
        pv_to_grid.append(_clean(export_power))
        pv_curtailment.append(_clean(curtailment))
        grid_import.append(_clean(max(load - local_pv, 0.0)))

    max_grid_import = _clean(max(grid_import))
    energy_cost = sum(
        dispatch_input.buy_price[t] * grid_import[t] * dispatch_input.step_hours
        for t in range(len(dispatch_input.timestamps))
    )
    demand_cost = dispatch_input.demand_charge_rate * max_grid_import
    sell_revenue = sum(
        dispatch_input.export.sell_price * pv_to_grid[t] * dispatch_input.step_hours
        for t in range(len(dispatch_input.timestamps))
    )
    curtailment_cost = sum(
        dispatch_input.export.curtailment_cost_rate
        * pv_curtailment[t]
        * dispatch_input.step_hours
        for t in range(len(dispatch_input.timestamps))
    )

    return UserSidePVDispatchResult(
        pv_to_load=pv_to_load,
        pv_to_grid=pv_to_grid,
        pv_curtailment=pv_curtailment,
        grid_import=grid_import,
        max_grid_import=max_grid_import,
        energy_cost=energy_cost,
        demand_cost=demand_cost,
        sell_revenue=sell_revenue,
        curtailment_cost=curtailment_cost,
        total_cost=energy_cost + demand_cost + curtailment_cost - sell_revenue,
        constraint_violations=_constraint_violations(
            dispatch_input,
            pv_to_load,
            pv_to_grid,
            pv_curtailment,
            grid_import,
            max_grid_import,
        ),
    )


def _validate_input(dispatch_input: UserSidePVDispatchInput) -> None:
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


def _export_power(surplus: float, export: UserSidePVExportParams) -> float:
    if not export.allow_export:
        return 0.0
    if export.export_limit is None:
        return surplus
    return min(surplus, export.export_limit)


def _constraint_violations(
    dispatch_input: UserSidePVDispatchInput,
    pv_to_load: list[float],
    pv_to_grid: list[float],
    pv_curtailment: list[float],
    grid_import: list[float],
    max_grid_import: float,
) -> dict[str, float]:
    tolerance = 1e-6
    violations = {
        "pv_balance": max(
            abs(pv_to_load[t] + pv_to_grid[t] + pv_curtailment[t] - pv)
            for t, pv in enumerate(dispatch_input.pv_forecast)
        ),
        "load_balance": max(
            abs(pv_to_load[t] + grid_import[t] - load)
            for t, load in enumerate(dispatch_input.load_forecast)
        ),
        "grid_import_min": max(-min(grid_import), 0.0),
        "max_grid_import": max(max(grid_import) - max_grid_import, 0.0),
    }
    return {
        name: amount for name, amount in violations.items() if amount > tolerance
    }


def _clean(raw_value: float) -> float:
    if abs(raw_value) < 1e-9:
        return 0.0
    return float(raw_value)
