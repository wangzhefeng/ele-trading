from __future__ import annotations

from .interfaces import (
    UserSideRenewableDispatchInput,
    UserSidePVDispatchInput,
    UserSidePVDispatchResult,
    UserSidePVExportParams,
)
from .user_side_renewable_dispatch_class import run_user_side_renewable_dispatch


def run_user_side_pv_dispatch(dispatch_input: UserSidePVDispatchInput) -> UserSidePVDispatchResult:
    """
    Run deterministic user-side PV dispatch without storage.
    """
    # TODO 补充注释
    _validate_input(dispatch_input)
    # TODO 补充注释
    renewable_result = run_user_side_renewable_dispatch(
        UserSideRenewableDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=dispatch_input.pv_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
        )
    )

    return UserSidePVDispatchResult(
        pv_to_load=renewable_result.renewable_to_load,
        pv_to_grid=renewable_result.renewable_to_grid,
        pv_curtailment=renewable_result.renewable_curtailment,
        grid_import=renewable_result.grid_import,
        max_grid_import=renewable_result.max_grid_import,
        energy_cost=renewable_result.energy_cost,
        demand_cost=renewable_result.demand_cost,
        sell_revenue=renewable_result.sell_revenue,
        curtailment_cost=renewable_result.curtailment_cost,
        total_cost=renewable_result.total_cost,
        constraint_violations=_pv_constraint_violations(
            renewable_result.constraint_violations
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


def _pv_constraint_violations(violations: dict[str, float]) -> dict[str, float]:
    return {
        ("pv_balance" if name == "renewable_balance" else name): amount
        for name, amount in violations.items()
    }
