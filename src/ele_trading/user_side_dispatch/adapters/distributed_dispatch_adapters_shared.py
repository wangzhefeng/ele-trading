"""分布式调度适配器共享逻辑。

原 ``_to_renewable_input`` 和 ``_to_shared_result_fields`` 的公共辅助,
从 ``distributed_dispatch_adapters.py`` 提取以避免循环导入。
"""

from __future__ import annotations

from ..interfaces import DistributedRenewableBESSDispatchInput


def _to_renewable_input(dispatch_input) -> DistributedRenewableBESSDispatchInput:
    """把场景 Input 透传为内核 Input(类型已统一,字段完全一致)。"""
    return dispatch_input


def _to_shared_result_fields(renewable_result) -> dict:
    """提取内核 Result 中的通用字段(PV/Wind/WindPV 共用)。"""
    return {
        "grid_to_load_by_node": renewable_result.grid_to_load_by_node,
        "grid_to_bess_by_node": renewable_result.grid_to_bess_by_node,
        "charge_power_by_node": renewable_result.charge_power_by_node,
        "discharge_power_by_node": renewable_result.discharge_power_by_node,
        "net_bess_power_by_node": renewable_result.net_bess_power_by_node,
        "soc_by_node": renewable_result.soc_by_node,
        "grid_import_total": renewable_result.grid_import_total,
        "transformer_import_by_node": renewable_result.transformer_import_by_node,
        "transformer_export_by_node": renewable_result.transformer_export_by_node,
        "renewable_allocation_by_source_target": renewable_result.renewable_allocation_by_source_target,
        "bess_allocation_by_source_target": renewable_result.bess_allocation_by_source_target,
        "max_demand_kw": renewable_result.max_demand_kw,
        "energy_cost": renewable_result.energy_cost,
        "demand_cost": renewable_result.demand_cost,
        "sell_revenue": renewable_result.sell_revenue,
        "curtailment_cost": renewable_result.curtailment_cost,
        "cross_flow_cost": renewable_result.cross_flow_cost,
        "cycle_cost": renewable_result.cycle_cost,
        "smooth_cost": renewable_result.smooth_cost,
        "soc_target_cost": renewable_result.soc_target_cost,
        "total_cost": renewable_result.total_cost,
        "solver_status": renewable_result.solver_status,
        "solver_name": renewable_result.solver_name,
        "constraint_violations": renewable_result.constraint_violations,
    }
