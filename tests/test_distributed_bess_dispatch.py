from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

import ele_trading.optimization as optimization
from ele_trading.capacity_planning import distributed_bess_planner as dist_module
from ele_trading.optimization.interfaces import (
    DistributedBESSDemandChargeConfig,
    DistributedBESSDispatchInput,
    DistributedBESSDispatchPolicy,
    DistributedBESSNodeParams,
)


def _timestamps() -> list[datetime]:
    return [
        datetime(2024, 1, 1, 0, 0),
        datetime(2024, 1, 1, 0, 15),
        datetime(2024, 1, 1, 0, 30),
        datetime(2024, 1, 1, 0, 45),
    ]


def _nodes() -> list[DistributedBESSNodeParams]:
    return [
        DistributedBESSNodeParams(
            name="n1",
            transformer_capacity_kw=100.0,
            bess_power_kw=50.0,
            bess_capacity_kwh=100.0,
            soc_min_kwh=0.0,
            soc_max_kwh=100.0,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        ),
        DistributedBESSNodeParams(
            name="n2",
            transformer_capacity_kw=100.0,
            bess_power_kw=50.0,
            bess_capacity_kwh=100.0,
            soc_min_kwh=0.0,
            soc_max_kwh=100.0,
            charge_efficiency=0.95,
            discharge_efficiency=0.95,
        ),
    ]


def _dispatch_input() -> DistributedBESSDispatchInput:
    return DistributedBESSDispatchInput(
        timestamps=_timestamps(),
        local_load_forecast=[
            [40.0, 40.0, 40.0, 40.0],
            [20.0, 20.0, 20.0, 20.0],
        ],
        system_load_forecast=[60.0, 60.0, 60.0, 60.0],
        buy_price=[0.2, 0.2, 0.8, 0.8],
        price_type=["谷", "谷", "峰", "峰"],
        nodes=_nodes(),
        initial_soc_kwh=[20.0, 20.0],
        step_hours=0.25,
        demand_charge_rate=10.0,
        grid_import_formula="park_baseline",
        grid_import_nonneg=True,
        demand_charge=DistributedBESSDemandChargeConfig(mode="point_max"),
        policy=DistributedBESSDispatchPolicy(
            charge_allowed_hours=[0],
            discharge_allowed_hours=[0],
        ),
    )


def test_package_exports_distributed_bess_dispatch_api():
    assert optimization.DistributedBESSNodeParams is DistributedBESSNodeParams
    assert optimization.DistributedBESSDispatchInput is DistributedBESSDispatchInput
    assert optimization.run_distributed_bess_dispatch is not None


def test_run_distributed_bess_dispatch_returns_expected_result_fields():
    result = optimization.run_distributed_bess_dispatch(_dispatch_input())

    assert len(result.charge_power_by_node) == 2
    assert len(result.discharge_power_by_node) == 2
    assert len(result.soc_by_node) == 2
    assert len(result.grid_import_total) == 4
    assert result.demand_cost >= 0.0
    assert result.total_cost == pytest.approx(
        result.energy_cost
        + result.demand_cost
        + result.cross_flow_cost
        + result.smooth_cost
        + result.soc_target_cost
    )


def test_run_distributed_bess_dispatch_supports_sliding_window_demand_charge():
    node = DistributedBESSNodeParams(
        name="n1",
        transformer_capacity_kw=100.0,
        bess_power_kw=0.0,
        bess_capacity_kwh=100.0,
        soc_min_kwh=0.0,
        soc_max_kwh=100.0,
    )
    point_result = optimization.run_distributed_bess_dispatch(
        DistributedBESSDispatchInput(
            timestamps=_timestamps(),
            local_load_forecast=[[0.0, 10.0, 0.0, 10.0]],
            system_load_forecast=[0.0, 10.0, 0.0, 10.0],
            buy_price=[1.0, 1.0, 1.0, 1.0],
            price_type=["平"] * 4,
            nodes=[node],
            initial_soc_kwh=[0.0],
            step_hours=0.25,
            demand_charge_rate=1.0,
            demand_charge=DistributedBESSDemandChargeConfig(mode="point_max"),
        )
    )
    window_result = optimization.run_distributed_bess_dispatch(
        DistributedBESSDispatchInput(
            timestamps=_timestamps(),
            local_load_forecast=[[0.0, 10.0, 0.0, 10.0]],
            system_load_forecast=[0.0, 10.0, 0.0, 10.0],
            buy_price=[1.0, 1.0, 1.0, 1.0],
            price_type=["平"] * 4,
            nodes=[node],
            initial_soc_kwh=[0.0],
            step_hours=0.25,
            demand_charge_rate=1.0,
            demand_charge=DistributedBESSDemandChargeConfig(
                mode="sliding_window",
                window_minutes=30,
            ),
        )
    )

    assert point_result.max_demand_kw == pytest.approx(10.0)
    assert window_result.max_demand_kw == pytest.approx(5.0)


def test_optimize_combo_uses_distributed_dispatcher(monkeypatch: pytest.MonkeyPatch):
    called = {"count": 0}

    class FakeDispatcher:
        def __init__(self, dispatch_input):
            self.dispatch_input = dispatch_input

        def solve(self):
            called["count"] += 1
            steps = len(self.dispatch_input.timestamps)
            node_count = len(self.dispatch_input.nodes)
            return type(
                "FakeResult",
                (),
                {
                    "charge_power_by_node": [[0.0] * steps for _ in range(node_count)],
                    "discharge_power_by_node": [[0.0] * steps for _ in range(node_count)],
                    "net_bess_power_by_node": [[0.0] * steps for _ in range(node_count)],
                    "soc_by_node": [[0.0] * steps for _ in range(node_count)],
                    "grid_to_load_by_node": [
                        self.dispatch_input.local_load_forecast[idx][:]
                        for idx in range(node_count)
                    ],
                    "grid_import_total": self.dispatch_input.system_load_forecast[:],
                    "transformer_import_by_node": [
                        self.dispatch_input.local_load_forecast[idx][:]
                        for idx in range(node_count)
                    ],
                    "transformer_export_by_node": [[0.0] * steps for _ in range(node_count)],
                    "allocation_by_source_target": [
                        [[0.0] * steps for _ in range(node_count)]
                        for _ in range(node_count)
                    ],
                    "total_cost": 0.0,
                },
            )()

    monkeypatch.setattr(
        dist_module,
        "DistributedBESSDispatcher",
        FakeDispatcher,
        raising=False,
    )

    time_index = pd.to_datetime(_timestamps())
    system_load = pd.Series([60.0, 60.0, 60.0, 60.0], index=time_index)
    local_loads = {
        "338_1": pd.Series([20.0, 20.0, 20.0, 20.0], index=time_index),
        "338_2": pd.Series([20.0, 20.0, 20.0, 20.0], index=time_index),
        "338_3": pd.Series([20.0, 20.0, 20.0, 20.0], index=time_index),
    }
    ele_price = pd.DataFrame(
        {
            "value": [0.2, 0.2, 0.8, 0.8],
            "type": ["谷", "谷", "峰", "峰"],
        },
        index=time_index,
    )

    schedule_df, objective_value = dist_module.optimize_combo(
        cabinet_counts=(1, 1, 1),
        system_config=dist_module.SYSTEMS["338"],
        system_load=system_load,
        local_loads=local_loads,
        ele_price=ele_price,
        max_demand_price=10.0,
        start_time=time_index[0].to_pydatetime(),
        end_time=datetime(2024, 1, 2, 0, 0),
        freq_minutes=15,
        scheduler_config=dist_module.V1_PRESET,
    )

    assert called["count"] == 1
    assert objective_value == 0.0
    assert "grid_import_total" in schedule_df.columns
    assert "power_338_1" in schedule_df.columns
    assert "allocation_338_1_to_338_2" in schedule_df.columns
