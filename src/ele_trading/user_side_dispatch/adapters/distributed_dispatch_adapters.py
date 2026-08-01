"""用户侧分布式调度场景适配层。

统一收录 PV+BESS / Wind+BESS / Wind+PV+BESS 三类多节点分布式场景的薄包装。
三类场景的 Input/Result 现在统一使用 ``DistributedRenewable*`` 类型,
PV/Wind 场景直接透传,Wind+PV 场景需在调用方完成 pv+wind 合并后传入
``renewable_forecast``。本模块依赖 CVXPY 内核,只能经包级 lazy 属性访问,
不能直接顶层导入(否则会拉起可选依赖 CVXPY)。
"""

from __future__ import annotations

from .distributed_dispatch_adapters_shared import (
    _to_renewable_input,
    _to_shared_result_fields,
)
from ..interfaces import (
    DistributedPVBESSDispatchInput,
    DistributedPVBESSDispatchResult,
    DistributedWindBESSDispatchInput,
    DistributedWindBESSDispatchResult,
    DistributedWindPVBESSDispatchInput,
    DistributedWindPVBESSDispatchResult,
)
from ..algorithms.user_side_renewable_bess_distributed_dispatch_class import (
    run_user_side_renewable_bess_distributed_dispatch,
)


def run_user_side_pv_bess_distributed_dispatch(
    dispatch_input: DistributedPVBESSDispatchInput,
) -> DistributedPVBESSDispatchResult:
    """PV+BESS 分布式调度。PV 场景的节点已用 renewable_forecast 传入,直接透传。"""
    renewable_result = run_user_side_renewable_bess_distributed_dispatch(
        _to_renewable_input(dispatch_input)
    )
    return DistributedPVBESSDispatchResult(
        renewable_to_load_by_node=renewable_result.renewable_to_load_by_node,
        renewable_to_bess_by_node=renewable_result.renewable_to_bess_by_node,
        renewable_to_grid_by_node=renewable_result.renewable_to_grid_by_node,
        renewable_curtailment_by_node=renewable_result.renewable_curtailment_by_node,
        **_to_shared_result_fields(renewable_result),
    )


def run_user_side_wind_bess_distributed_dispatch(
    dispatch_input: DistributedWindBESSDispatchInput,
) -> DistributedWindBESSDispatchResult:
    """Wind+BESS 分布式调度。Wind 场景的节点已用 renewable_forecast 传入,直接透传。"""
    renewable_result = run_user_side_renewable_bess_distributed_dispatch(
        _to_renewable_input(dispatch_input)
    )
    return DistributedWindBESSDispatchResult(
        renewable_to_load_by_node=renewable_result.renewable_to_load_by_node,
        renewable_to_bess_by_node=renewable_result.renewable_to_bess_by_node,
        renewable_to_grid_by_node=renewable_result.renewable_to_grid_by_node,
        renewable_curtailment_by_node=renewable_result.renewable_curtailment_by_node,
        **_to_shared_result_fields(renewable_result),
    )


def run_user_side_wind_pv_bess_distributed_dispatch(
    dispatch_input: DistributedWindPVBESSDispatchInput,
) -> DistributedWindPVBESSDispatchResult:
    """Wind+PV+BESS 分布式调度。合并 pv+wind 后委托内核。"""
    from ..interfaces import (
        DistributedRenewableBESSDispatchInput,
        DistributedRenewableBESSNodeInput,
    )
    renewable_forecast_by_node = [
        [round(pv + wind, 6) for pv, wind in zip(node.pv_forecast, node.wind_forecast)]
        for node in dispatch_input.nodes
    ]
    renewable_result = run_user_side_renewable_bess_distributed_dispatch(
        DistributedRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            nodes=[
                DistributedRenewableBESSNodeInput(
                    name=node.name,
                    transformer_capacity_kw=node.transformer_capacity_kw,
                    load_forecast=node.load_forecast,
                    renewable_forecast=renewable_forecast_by_node[idx],
                    bess_power_kw=node.bess_power_kw,
                    bess_capacity_kwh=node.bess_capacity_kwh,
                    soc_min_kwh=node.soc_min_kwh,
                    soc_max_kwh=node.soc_max_kwh,
                    charge_efficiency=node.charge_efficiency,
                    discharge_efficiency=node.discharge_efficiency,
                )
                for idx, node in enumerate(dispatch_input.nodes)
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
    return DistributedWindPVBESSDispatchResult(
        pv_forecast_by_node=[node.pv_forecast for node in dispatch_input.nodes],
        wind_forecast_by_node=[node.wind_forecast for node in dispatch_input.nodes],
        renewable_forecast_by_node=renewable_forecast_by_node,
        renewable_to_load_by_node=renewable_result.renewable_to_load_by_node,
        renewable_to_bess_by_node=renewable_result.renewable_to_bess_by_node,
        renewable_to_grid_by_node=renewable_result.renewable_to_grid_by_node,
        renewable_curtailment_by_node=renewable_result.renewable_curtailment_by_node,
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
