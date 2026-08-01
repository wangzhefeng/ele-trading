"""用户侧储能滚动调度模型测试。"""

import pytest

import ele_trading.user_side_dispatch as optimization
from ele_trading.user_side_dispatch.algorithms.user_side_bess_dispatch_pulp import (
    UserSideBESSDispatchInput,
    UserSideBESSParams,
    run_user_side_bess_dispatch,
)


def _storage() -> UserSideBESSParams:
    return UserSideBESSParams(
        capacity=10.0,
        soc_min=1.0,
        soc_max=10.0,
        p_ch_max=4.0,
        p_dis_max=4.0,
        eta_ch=1.0,
        eta_dis=1.0,
    )


def _dispatch_input(
    load_forecast,
    buy_price,
    *,
    demand_charge_rate=0.0,
    initial_soc=5.0,
    terminal_soc_target=None,
    cycle_cost_rate=0.0,
):
    return UserSideBESSDispatchInput(
        timestamps=list(range(len(load_forecast))),
        load_forecast=list(load_forecast),
        buy_price=list(buy_price),
        price_type=["flat"] * len(load_forecast),
        bess=_storage(),
        initial_soc=initial_soc,
        demand_charge_rate=demand_charge_rate,
        step_hours=1.0,
        terminal_soc_target=terminal_soc_target,
        cycle_cost_rate=cycle_cost_rate,
    )


def test_user_side_dispatch_returns_expected_fields():
    """调度结果应返回用户侧电表功率、SOC、成本和约束校验。"""
    result = run_user_side_bess_dispatch(
        _dispatch_input(
            load_forecast=[5.0, 5.0, 5.0, 5.0],
            buy_price=[1.0, 0.2, 0.2, 1.0],
            terminal_soc_target=5.0,
        )
    )

    assert len(result.charge_power) == 4
    assert len(result.discharge_power) == 4
    assert len(result.net_bess_power) == 4
    assert len(result.soc) == 4
    assert len(result.grid_import) == 4
    assert result.max_grid_import == pytest.approx(max(result.grid_import))
    assert result.energy_cost >= 0.0
    assert result.demand_cost >= 0.0
    assert result.total_cost == pytest.approx(result.energy_cost + result.demand_cost)
    assert result.constraint_violations == {}


def test_demand_charge_reduces_peak_grid_import():
    """需量电费较高时，模型应削减最大电表购电功率。"""
    no_demand = run_user_side_bess_dispatch(
        _dispatch_input(
            load_forecast=[4.0, 4.0, 10.0, 4.0],
            buy_price=[0.5, 0.5, 0.5, 0.5],
            demand_charge_rate=0.0,
            initial_soc=5.0,
        )
    )
    with_demand = run_user_side_bess_dispatch(
        _dispatch_input(
            load_forecast=[4.0, 4.0, 10.0, 4.0],
            buy_price=[0.5, 0.5, 0.5, 0.5],
            demand_charge_rate=20.0,
            initial_soc=5.0,
        )
    )

    assert with_demand.max_grid_import < no_demand.max_grid_import
    assert with_demand.demand_cost == pytest.approx(
        with_demand.max_grid_import * 20.0
    )


def test_price_spread_charges_low_and_discharges_high_without_export():
    """峰谷价差场景下应低价充电、高价放电，且不反送电。"""
    result = run_user_side_bess_dispatch(
        _dispatch_input(
            load_forecast=[3.0, 3.0, 3.0, 3.0],
            buy_price=[1.0, 0.1, 0.1, 1.0],
            initial_soc=3.0,
            terminal_soc_target=3.0,
        )
    )

    assert result.charge_power[1] + result.charge_power[2] > 0.0
    assert result.discharge_power[0] + result.discharge_power[3] > 0.0
    assert min(result.grid_import) >= -1e-7
    for discharge, load in zip(result.discharge_power, [3.0, 3.0, 3.0, 3.0]):
        assert discharge <= load + 1e-7


def test_same_prices_different_loads_produce_different_dispatch():
    """同一价格曲线下，用户侧模型应随负荷预测变化而改变调度。"""
    prices = [0.2, 0.2, 1.0, 1.0]
    low_load = run_user_side_bess_dispatch(
        _dispatch_input(
            load_forecast=[1.0, 1.0, 1.0, 1.0],
            buy_price=prices,
            initial_soc=3.0,
            terminal_soc_target=3.0,
        )
    )
    high_load = run_user_side_bess_dispatch(
        _dispatch_input(
            load_forecast=[5.0, 5.0, 5.0, 5.0],
            buy_price=prices,
            initial_soc=3.0,
            terminal_soc_target=3.0,
        )
    )

    assert low_load.discharge_power != pytest.approx(high_load.discharge_power)
    assert low_load.grid_import != pytest.approx(high_load.grid_import)


def test_input_lengths_must_match():
    """时间、电价、电价类型和负荷预测长度必须一致。"""
    with pytest.raises(ValueError, match="same length"):
        run_user_side_bess_dispatch(
            UserSideBESSDispatchInput(
                timestamps=[0, 1],
                load_forecast=[5.0, 5.0],
                buy_price=[0.2],
                price_type=["flat", "peak"],
                bess=_storage(),
                initial_soc=5.0,
                demand_charge_rate=0.0,
                step_hours=1.0,
            )
        )


def test_package_exports_user_side_dispatch_api():
    """optimization 包级入口应导出用户侧调度 API。"""
    assert optimization.UserSideBESSDispatchInput is UserSideBESSDispatchInput
    assert optimization.UserSideBESSParams is UserSideBESSParams
    assert optimization.run_user_side_bess_dispatch is run_user_side_bess_dispatch
