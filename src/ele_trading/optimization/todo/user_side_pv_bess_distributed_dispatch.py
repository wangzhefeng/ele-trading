from __future__ import annotations

from .interfaces import (
    DistributedPVBESSDispatchInput,
    DistributedPVBESSDispatchResult,
    DistributedRenewableBESSDispatchInput,
    DistributedRenewableBESSNodeInput,
)
from .user_side_renewable_bess_distributed_dispatch_class import (
    run_user_side_renewable_bess_distributed_dispatch,
)


def run_user_side_pv_bess_distributed_dispatch(
    dispatch_input: DistributedPVBESSDispatchInput,
) -> DistributedPVBESSDispatchResult:
    renewable_result = run_user_side_renewable_bess_distributed_dispatch(
        DistributedRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            nodes=[
                DistributedRenewableBESSNodeInput(
                    name=node.name,
                    transformer_capacity_kw=node.transformer_capacity_kw,
                    load_forecast=node.load_forecast,
                    renewable_forecast=node.pv_forecast,
                    bess_power_kw=node.bess_power_kw,
                    bess_capacity_kwh=node.bess_capacity_kwh,
                    soc_min_kwh=node.soc_min_kwh,
                    soc_max_kwh=node.soc_max_kwh,
                    charge_efficiency=node.charge_efficiency,
                    discharge_efficiency=node.discharge_efficiency,
                )
                for node in dispatch_input.nodes
            ],
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            initial_soc_kwh=dispatch_input.initial_soc_kwh,
            step_hours=dispatch_input.step_hours,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            demand_charge=dispatch_input.demand_charge,
            export=dispatch_input.export,
            policy=dispatch_input.policy,
            grid_import_formula=dispatch_input.grid_import_formula,
            grid_import_nonneg=dispatch_input.grid_import_nonneg,
            cycle_cost_rate=dispatch_input.cycle_cost_rate,
            solver=dispatch_input.solver,
        )
    )
    return DistributedPVBESSDispatchResult(
        pv_to_load_by_node=renewable_result.renewable_to_load_by_node,
        pv_to_bess_by_node=renewable_result.renewable_to_bess_by_node,
        pv_to_grid_by_node=renewable_result.renewable_to_grid_by_node,
        pv_curtailment_by_node=renewable_result.renewable_curtailment_by_node,
        grid_to_load_by_node=renewable_result.grid_to_load_by_node,
        grid_to_bess_by_node=renewable_result.grid_to_bess_by_node,
        charge_power_by_node=renewable_result.charge_power_by_node,
        discharge_power_by_node=renewable_result.discharge_power_by_node,
        net_bess_power_by_node=renewable_result.net_bess_power_by_node,
        soc_by_node=renewable_result.soc_by_node,
        grid_import_total=renewable_result.grid_import_total,
        max_demand_kw=renewable_result.max_demand_kw,
        energy_cost=renewable_result.energy_cost,
        demand_cost=renewable_result.demand_cost,
        sell_revenue=renewable_result.sell_revenue,
        curtailment_cost=renewable_result.curtailment_cost,
        cross_flow_cost=renewable_result.cross_flow_cost,
        cycle_cost=renewable_result.cycle_cost,
        smooth_cost=renewable_result.smooth_cost,
        soc_target_cost=renewable_result.soc_target_cost,
        total_cost=renewable_result.total_cost,
        constraint_violations=renewable_result.constraint_violations,
    )
