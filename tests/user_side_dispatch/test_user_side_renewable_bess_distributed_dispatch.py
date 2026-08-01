from __future__ import annotations

from datetime import datetime

import pytest

import ele_trading.user_side_dispatch as optimization
from ele_trading.user_side_dispatch.interfaces import (
    DistributedBESSDemandChargeConfig,
    DistributedPVBESSDispatchInput,
    DistributedPVBESSNodeInput,
    DistributedRenewableBESSDispatchInput,
    DistributedRenewableBESSDispatchPolicy,
    DistributedRenewableBESSNodeInput,
    DistributedRenewableExportConfig,
    DistributedWindBESSDispatchInput,
    DistributedWindBESSNodeInput,
    DistributedWindPVBESSDispatchInput,
    DistributedWindPVBESSNodeInput,
)


def _timestamps() -> list[datetime]:
    return [
        datetime(2024, 1, 1, 18, 0),
        datetime(2024, 1, 1, 18, 15),
    ]


def _export(*, allow_export: bool) -> DistributedRenewableExportConfig:
    return DistributedRenewableExportConfig(
        allow_export=allow_export,
        sell_price=0.5,
        curtailment_cost_rate=0.1,
    )


def test_package_exports_distributed_renewable_bess_api():
    assert optimization.DistributedRenewableBESSDispatchInput is DistributedRenewableBESSDispatchInput
    assert optimization.DistributedRenewableBESSNodeInput is DistributedRenewableBESSNodeInput
    assert optimization.run_user_side_renewable_bess_distributed_dispatch is not None
    assert optimization.run_user_side_pv_bess_distributed_dispatch is not None
    assert optimization.run_user_side_wind_bess_distributed_dispatch is not None
    assert optimization.run_user_side_wind_pv_bess_distributed_dispatch is not None


def test_renewable_distributed_dispatch_shares_energy_across_transformers():
    dispatch_input = DistributedRenewableBESSDispatchInput(
        timestamps=_timestamps(),
        nodes=[
            DistributedRenewableBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[2.0, 2.0],
                renewable_forecast=[8.0, 8.0],
                bess_power_kw=0.0,
                bess_capacity_kwh=1.0,
                soc_min_kwh=0.0,
                soc_max_kwh=1.0,
            ),
            DistributedRenewableBESSNodeInput(
                name="n2",
                transformer_capacity_kw=20.0,
                load_forecast=[5.0, 5.0],
                renewable_forecast=[0.0, 0.0],
                bess_power_kw=0.0,
                bess_capacity_kwh=1.0,
                soc_min_kwh=0.0,
                soc_max_kwh=1.0,
            ),
        ],
        buy_price=[1.0, 1.0],
        price_type=["平", "平"],
        initial_soc_kwh=[0.0, 0.0],
        step_hours=0.25,
        demand_charge_rate=0.0,
        demand_charge=DistributedBESSDemandChargeConfig(mode="point_max"),
        export=_export(allow_export=False),
        policy=DistributedRenewableBESSDispatchPolicy(
            renewable_cross_transformer_support=True,
        ),
    )

    result = optimization.run_user_side_renewable_bess_distributed_dispatch(dispatch_input)

    assert result.renewable_to_load_by_node[0] == pytest.approx([2.0, 2.0])
    assert result.renewable_to_load_by_node[1] == pytest.approx([5.0, 5.0])
    assert result.renewable_allocation_by_source_target[0][1][0] > 0.0
    assert result.grid_import_total == pytest.approx([0.0, 0.0])
    for idx, node in enumerate(dispatch_input.nodes):
        for t in range(len(dispatch_input.timestamps)):
            used = (
                sum(result.renewable_allocation_by_source_target[idx][target][t] for target in range(len(dispatch_input.nodes)))
                + result.renewable_to_grid_by_node[idx][t]
                + result.renewable_curtailment_by_node[idx][t]
            )
            assert used == pytest.approx(node.renewable_forecast[t])


def test_renewable_distributed_dispatch_allows_bess_cross_support():
    dispatch_input = DistributedRenewableBESSDispatchInput(
        timestamps=_timestamps(),
        nodes=[
            DistributedRenewableBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[0.0, 0.0],
                renewable_forecast=[0.0, 0.0],
                bess_power_kw=5.0,
                bess_capacity_kwh=10.0,
                soc_min_kwh=0.0,
                soc_max_kwh=10.0,
            ),
            DistributedRenewableBESSNodeInput(
                name="n2",
                transformer_capacity_kw=20.0,
                load_forecast=[4.0, 4.0],
                renewable_forecast=[0.0, 0.0],
                bess_power_kw=0.0,
                bess_capacity_kwh=1.0,
                soc_min_kwh=0.0,
                soc_max_kwh=1.0,
            ),
        ],
        buy_price=[1.0, 1.0],
        price_type=["峰", "峰"],
        initial_soc_kwh=[4.0, 0.0],
        step_hours=0.25,
        demand_charge_rate=0.0,
        demand_charge=DistributedBESSDemandChargeConfig(mode="point_max"),
        export=_export(allow_export=False),
        policy=DistributedRenewableBESSDispatchPolicy(
            charge_allowed_hours=[],
            discharge_allowed_hours=[18],
        ),
        cycle_cost_rate=0.0,
    )

    result = optimization.run_user_side_renewable_bess_distributed_dispatch(dispatch_input)

    assert result.bess_allocation_by_source_target[0][1][0] > 0.0
    assert result.discharge_power_by_node[0][0] > 0.0
    assert result.grid_import_total[0] < 4.0


def test_renewable_distributed_dispatch_export_reduces_total_cost():
    base_kwargs = dict(
        timestamps=_timestamps(),
        nodes=[
            DistributedRenewableBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[1.0, 1.0],
                renewable_forecast=[6.0, 6.0],
                bess_power_kw=0.0,
                bess_capacity_kwh=1.0,
                soc_min_kwh=0.0,
                soc_max_kwh=1.0,
            )
        ],
        buy_price=[1.0, 1.0],
        price_type=["平", "平"],
        initial_soc_kwh=[0.0],
        step_hours=0.25,
        demand_charge_rate=0.0,
        demand_charge=DistributedBESSDemandChargeConfig(mode="point_max"),
        policy=DistributedRenewableBESSDispatchPolicy(),
        cycle_cost_rate=0.0,
    )
    no_export = optimization.run_user_side_renewable_bess_distributed_dispatch(
        DistributedRenewableBESSDispatchInput(
            export=_export(allow_export=False),
            **base_kwargs,
        )
    )
    with_export = optimization.run_user_side_renewable_bess_distributed_dispatch(
        DistributedRenewableBESSDispatchInput(
            export=_export(allow_export=True),
            **base_kwargs,
        )
    )

    assert sum(with_export.renewable_to_grid_by_node[0]) > 0.0
    assert with_export.total_cost < no_export.total_cost


def test_renewable_distributed_dispatch_sliding_window_demand():
    dispatch_input = DistributedRenewableBESSDispatchInput(
        timestamps=[
            datetime(2024, 1, 1, 18, 0),
            datetime(2024, 1, 1, 18, 5),
            datetime(2024, 1, 1, 18, 10),
        ],
        nodes=[
            DistributedRenewableBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[0.0, 0.0, 9.0],
                renewable_forecast=[0.0, 0.0, 0.0],
                bess_power_kw=0.0,
                bess_capacity_kwh=1.0,
                soc_min_kwh=0.0,
                soc_max_kwh=1.0,
            )
        ],
        buy_price=[1.0, 1.0, 1.0],
        price_type=["平", "平", "平"],
        initial_soc_kwh=[0.0],
        step_hours=5 / 60,
        demand_charge_rate=10.0,
        demand_charge=DistributedBESSDemandChargeConfig(
            mode="sliding_window",
            window_minutes=15,
        ),
        export=_export(allow_export=False),
        cycle_cost_rate=0.0,
    )

    result = optimization.run_user_side_renewable_bess_distributed_dispatch(dispatch_input)

    assert result.grid_import_total == pytest.approx([0.0, 0.0, 9.0])
    assert result.max_demand_kw == pytest.approx(3.0)
    assert result.demand_cost == pytest.approx(30.0)


def test_pv_bess_distributed_adapter_matches_generic_renewable_kernel():
    dispatch_input = DistributedPVBESSDispatchInput(
        timestamps=_timestamps(),
        nodes=[
            DistributedPVBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[1.0, 1.0],
                renewable_forecast=[3.0, 0.0],
                bess_power_kw=5.0,
                bess_capacity_kwh=10.0,
                soc_min_kwh=0.0,
                soc_max_kwh=10.0,
            )
        ],
        buy_price=[0.2, 1.0],
        price_type=["谷", "峰"],
        initial_soc_kwh=[0.0],
        step_hours=0.25,
        demand_charge_rate=0.0,
        export=_export(allow_export=False),
        cycle_cost_rate=0.0,
    )

    pv_result = optimization.run_user_side_pv_bess_distributed_dispatch(dispatch_input)
    renewable_result = optimization.run_user_side_renewable_bess_distributed_dispatch(
        DistributedRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            nodes=[
                DistributedRenewableBESSNodeInput(
                    name=node.name,
                    transformer_capacity_kw=node.transformer_capacity_kw,
                    load_forecast=node.load_forecast,
                    renewable_forecast=node.renewable_forecast,
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
            export=dispatch_input.export,
            cycle_cost_rate=dispatch_input.cycle_cost_rate,
        )
    )

    assert pv_result.renewable_to_load_by_node == renewable_result.renewable_to_load_by_node
    assert pv_result.total_cost == pytest.approx(renewable_result.total_cost)


def test_wind_and_wind_pv_distributed_adapters_preserve_source_shapes():
    wind_input = DistributedWindBESSDispatchInput(
        timestamps=_timestamps(),
        nodes=[
            DistributedWindBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[1.0, 1.0],
                renewable_forecast=[3.0, 0.0],
                bess_power_kw=5.0,
                bess_capacity_kwh=10.0,
                soc_min_kwh=0.0,
                soc_max_kwh=10.0,
            )
        ],
        buy_price=[0.2, 1.0],
        price_type=["谷", "峰"],
        initial_soc_kwh=[0.0],
        step_hours=0.25,
        demand_charge_rate=0.0,
        export=_export(allow_export=False),
        cycle_cost_rate=0.0,
    )
    wind_pv_input = DistributedWindPVBESSDispatchInput(
        timestamps=_timestamps(),
        nodes=[
            DistributedWindPVBESSNodeInput(
                name="n1",
                transformer_capacity_kw=20.0,
                load_forecast=[1.0, 1.0],
                pv_forecast=[1.0, 0.0],
                wind_forecast=[2.0, 0.0],
                bess_power_kw=5.0,
                bess_capacity_kwh=10.0,
                soc_min_kwh=0.0,
                soc_max_kwh=10.0,
            )
        ],
        buy_price=[0.2, 1.0],
        price_type=["谷", "峰"],
        initial_soc_kwh=[0.0],
        step_hours=0.25,
        demand_charge_rate=0.0,
        export=_export(allow_export=False),
        cycle_cost_rate=0.0,
    )

    wind_result = optimization.run_user_side_wind_bess_distributed_dispatch(wind_input)
    wind_pv_result = optimization.run_user_side_wind_pv_bess_distributed_dispatch(wind_pv_input)

    assert wind_result.renewable_to_load_by_node[0][0] >= 0.0
    assert wind_pv_result.pv_forecast_by_node[0] == pytest.approx([1.0, 0.0])
    assert wind_pv_result.wind_forecast_by_node[0] == pytest.approx([2.0, 0.0])
    assert wind_pv_result.renewable_forecast_by_node[0] == pytest.approx([3.0, 0.0])
