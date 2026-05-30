"""用户侧 / 园区侧光伏+储能调度模型测试。"""

import pandas as pd
import pytest

import ele_trading.optimization as optimization
from ele_trading.optimization.user_side_pv_bess_dispatch import (
    UserSideDispatchPolicy,
    UserSidePVExportParams,
    UserSidePVBESSDispatchInput,
    UserSideBESSParams,
    run_user_side_pv_bess_dispatch,
)


def _storage() -> UserSideBESSParams:
    return UserSideBESSParams(
        capacity=12.0,
        soc_min=1.0,
        soc_max=12.0,
        p_ch_max=5.0,
        p_dis_max=5.0,
        eta_ch=1.0,
        eta_dis=1.0,
    )


def _dispatch_input(
    *,
    load_forecast,
    pv_forecast,
    buy_price=None,
    allow_export=True,
    export_limit=None,
    sell_price=0.2,
    demand_charge_rate=0.0,
    initial_soc=4.0,
    terminal_soc_target=None,
    cycle_cost_rate=0.0,
    policy=None,
):
    timestamps = pd.date_range("2026-01-01", periods=len(load_forecast), freq="h")
    return UserSidePVBESSDispatchInput(
        timestamps=timestamps.tolist(),
        load_forecast=list(load_forecast),
        pv_forecast=list(pv_forecast),
        buy_price=list(buy_price or [0.5] * len(load_forecast)),
        price_type=["flat"] * len(load_forecast),
        export=UserSidePVExportParams(
            allow_export=allow_export,
            sell_price=sell_price,
            export_limit=export_limit,
        ),
        demand_charge_rate=demand_charge_rate,
        step_hours=1.0,
        bess=_storage(),
        initial_soc=initial_soc,
        terminal_soc_target=terminal_soc_target,
        cycle_cost_rate=cycle_cost_rate,
        policy=policy,
    )


def test_pv_bess_dispatch_energy_balance_and_soc():
    dispatch_input = _dispatch_input(
        load_forecast=[5.0, 5.0, 5.0, 5.0],
        pv_forecast=[0.0, 8.0, 8.0, 0.0],
        buy_price=[1.0, 0.1, 0.1, 1.0],
        allow_export=False,
        initial_soc=2.0,
        terminal_soc_target=2.0,
    )

    result = run_user_side_pv_bess_dispatch(dispatch_input)

    for t in range(len(dispatch_input.timestamps)):
        assert (
            result.pv_to_load[t]
            + result.pv_to_bess[t]
            + result.pv_to_grid[t]
            + result.pv_curtailment[t]
        ) == pytest.approx(dispatch_input.pv_forecast[t])
        assert (
            result.pv_to_load[t]
            + result.discharge_power[t]
            + result.grid_to_load[t]
        ) == pytest.approx(dispatch_input.load_forecast[t])
        assert result.grid_import[t] == pytest.approx(
            result.grid_to_load[t] + result.grid_to_bess[t]
        )
        assert result.charge_power[t] == pytest.approx(
            result.pv_to_bess[t] + result.grid_to_bess[t]
        )
        assert result.charge_power[t] * result.discharge_power[t] == pytest.approx(0.0)

    assert min(result.soc) >= dispatch_input.bess.soc_min - 1e-7
    assert max(result.soc) <= dispatch_input.bess.soc_max + 1e-7
    assert result.constraint_violations == {}


def test_demand_charge_reduces_peak_grid_import_with_bess():
    base = _dispatch_input(
        load_forecast=[4.0, 4.0, 10.0, 4.0],
        pv_forecast=[0.0, 0.0, 0.0, 0.0],
        demand_charge_rate=0.0,
        initial_soc=6.0,
    )
    peak_shaving = _dispatch_input(
        load_forecast=[4.0, 4.0, 10.0, 4.0],
        pv_forecast=[0.0, 0.0, 0.0, 0.0],
        demand_charge_rate=20.0,
        initial_soc=6.0,
    )

    no_demand = run_user_side_pv_bess_dispatch(base)
    with_demand = run_user_side_pv_bess_dispatch(peak_shaving)

    assert with_demand.max_grid_import < no_demand.max_grid_import
    assert with_demand.demand_cost == pytest.approx(
        with_demand.max_grid_import * 20.0
    )


def test_export_policy_controls_pv_to_grid():
    allow_export = run_user_side_pv_bess_dispatch(
        _dispatch_input(
            load_forecast=[1.0],
            pv_forecast=[8.0],
            allow_export=True,
            export_limit=2.0,
            initial_soc=12.0,
        )
    )
    no_export = run_user_side_pv_bess_dispatch(
        _dispatch_input(
            load_forecast=[1.0],
            pv_forecast=[8.0],
            allow_export=False,
            initial_soc=12.0,
        )
    )

    assert allow_export.pv_to_grid == pytest.approx([2.0])
    assert allow_export.sell_revenue > 0.0
    assert no_export.pv_to_grid == pytest.approx([0.0])


def test_optional_dispatch_policy_limits_windows():
    policy = UserSideDispatchPolicy(
        charge_allowed_hours=[1],
        discharge_allowed_hours=[3],
        pv_to_bess_reward_rate=0.1,
    )
    result = run_user_side_pv_bess_dispatch(
        _dispatch_input(
            load_forecast=[4.0, 4.0, 4.0, 4.0],
            pv_forecast=[8.0, 8.0, 8.0, 0.0],
            allow_export=False,
            initial_soc=2.0,
            policy=policy,
        )
    )

    assert result.charge_power[0] == pytest.approx(0.0)
    assert result.charge_power[2] == pytest.approx(0.0)
    assert result.discharge_power[0] == pytest.approx(0.0)
    assert result.discharge_power[1] == pytest.approx(0.0)
    assert result.discharge_power[2] == pytest.approx(0.0)


def test_same_price_different_pv_changes_dispatch():
    low_pv = run_user_side_pv_bess_dispatch(
        _dispatch_input(
            load_forecast=[4.0, 4.0, 4.0, 4.0],
            pv_forecast=[0.0, 0.0, 0.0, 0.0],
            buy_price=[0.2, 0.2, 1.0, 1.0],
            initial_soc=3.0,
            terminal_soc_target=3.0,
        )
    )
    high_pv = run_user_side_pv_bess_dispatch(
        _dispatch_input(
            load_forecast=[4.0, 4.0, 4.0, 4.0],
            pv_forecast=[0.0, 8.0, 8.0, 0.0],
            buy_price=[0.2, 0.2, 1.0, 1.0],
            initial_soc=3.0,
            terminal_soc_target=3.0,
        )
    )

    assert low_pv.grid_import != pytest.approx(high_pv.grid_import)


def test_package_exports_user_side_pv_bess_api():
    assert optimization.UserSidePVBESSDispatchInput is UserSidePVBESSDispatchInput
    assert optimization.UserSideDispatchPolicy is UserSideDispatchPolicy
    assert optimization.run_user_side_pv_bess_dispatch is run_user_side_pv_bess_dispatch
