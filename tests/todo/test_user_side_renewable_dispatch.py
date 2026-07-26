import pytest

from ele_trading.optimization.todo import (
    UserSideBESSParams,
    UserSidePVExportParams,
    UserSideRenewableBESSDispatchInput,
    UserSideRenewableDispatchInput,
    UserSideWindBESSDispatchInput,
    UserSideWindDispatchInput,
    UserSideWindPVBESSDispatchInput,
    run_user_side_renewable_bess_dispatch,
)
from ele_trading.optimization.todo.user_side_renewable_dispatch_class import (
    run_user_side_renewable_dispatch,
)
from ele_trading.optimization.todo.user_side_wind_bess_dispatch import (
    run_user_side_wind_bess_dispatch,
)
from ele_trading.optimization.todo.user_side_wind_dispatch import run_user_side_wind_dispatch
from ele_trading.optimization.todo.user_side_wind_pv_bess_dispatch import (
    run_user_side_wind_pv_bess_dispatch,
)


def _bess() -> UserSideBESSParams:
    return UserSideBESSParams(
        capacity=10.0,
        soc_min=0.0,
        soc_max=10.0,
        p_ch_max=5.0,
        p_dis_max=5.0,
        eta_ch=1.0,
        eta_dis=1.0,
    )


def test_wind_dispatch_exports_surplus_and_limits_grid_import_peak():
    dispatch_input = UserSideWindDispatchInput(
        timestamps=[0, 1, 2],
        load_forecast=[5.0, 5.0, 8.0],
        wind_forecast=[3.0, 7.0, 10.0],
        buy_price=[1.0, 1.0, 1.0],
        price_type=["flat", "flat", "flat"],
        export=UserSidePVExportParams(allow_export=True, sell_price=0.2, export_limit=1.0),
        demand_charge_rate=10.0,
        step_hours=1.0,
    )

    result = run_user_side_wind_dispatch(dispatch_input)

    assert result.wind_to_load == pytest.approx([3.0, 5.0, 8.0])
    assert result.wind_to_grid == pytest.approx([0.0, 1.0, 1.0])
    assert result.wind_curtailment == pytest.approx([0.0, 1.0, 1.0])
    assert result.grid_import == pytest.approx([2.0, 0.0, 0.0])
    assert result.max_grid_import == pytest.approx(2.0)
    assert result.demand_cost == pytest.approx(20.0)
    assert result.constraint_violations == {}

    renewable_result = run_user_side_renewable_dispatch(
        UserSideRenewableDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=dispatch_input.wind_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
        )
    )
    assert result.wind_to_load == pytest.approx(renewable_result.renewable_to_load)
    assert result.total_cost == pytest.approx(renewable_result.total_cost)


def test_wind_dispatch_curtails_surplus_when_export_is_disabled():
    dispatch_input = UserSideWindDispatchInput(
        timestamps=[0],
        load_forecast=[4.0],
        wind_forecast=[9.0],
        buy_price=[1.0],
        price_type=["flat"],
        export=UserSidePVExportParams(allow_export=False, sell_price=0.0),
        demand_charge_rate=0.0,
        step_hours=1.0,
    )

    result = run_user_side_wind_dispatch(dispatch_input)

    assert result.wind_to_load == pytest.approx([4.0])
    assert result.wind_to_grid == pytest.approx([0.0])
    assert result.wind_curtailment == pytest.approx([5.0])
    assert result.grid_import == pytest.approx([0.0])


def test_wind_bess_dispatch_charges_surplus_wind_and_later_discharges():
    dispatch_input = UserSideWindBESSDispatchInput(
        timestamps=[0, 1, 2],
        load_forecast=[1.0, 5.0, 5.0],
        wind_forecast=[6.0, 0.0, 0.0],
        buy_price=[0.1, 1.0, 1.0],
        price_type=["valley", "peak", "peak"],
        export=UserSidePVExportParams(allow_export=False, sell_price=0.0),
        demand_charge_rate=0.0,
        step_hours=1.0,
        bess=_bess(),
        initial_soc=0.0,
        terminal_soc_target=0.0,
    )

    result = run_user_side_wind_bess_dispatch(dispatch_input)
    renewable_result = run_user_side_renewable_bess_dispatch(
        UserSideRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=dispatch_input.wind_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
            bess=dispatch_input.bess,
            initial_soc=dispatch_input.initial_soc,
            terminal_soc_target=dispatch_input.terminal_soc_target,
            cycle_cost_rate=dispatch_input.cycle_cost_rate,
            policy=dispatch_input.policy,
        )
    )

    assert result.wind_to_bess[0] > 0.0
    assert result.charge_power[0] > 0.0
    assert sum(result.discharge_power[1:]) > 0.0
    assert result.soc[-1] == pytest.approx(0.0)
    assert result.grid_import == pytest.approx(renewable_result.grid_import)
    assert result.constraint_violations == {}


def test_wind_pv_bess_dispatch_preserves_sources_and_matches_renewable_bess():
    common = dict(
        timestamps=[0, 1, 2],
        load_forecast=[1.0, 5.0, 5.0],
        buy_price=[0.1, 1.0, 1.0],
        price_type=["valley", "peak", "peak"],
        export=UserSidePVExportParams(allow_export=False, sell_price=0.0),
        demand_charge_rate=0.0,
        step_hours=1.0,
        bess=_bess(),
        initial_soc=0.0,
        terminal_soc_target=0.0,
    )
    wind_pv_input = UserSideWindPVBESSDispatchInput(
        pv_forecast=[2.0, 0.0, 0.0],
        wind_forecast=[4.0, 0.0, 0.0],
        **common,
    )
    renewable_input = UserSideRenewableBESSDispatchInput(
        renewable_forecast=[6.0, 0.0, 0.0],
        **common,
    )

    wind_pv_result = run_user_side_wind_pv_bess_dispatch(wind_pv_input)
    renewable_result = run_user_side_renewable_bess_dispatch(renewable_input)

    assert wind_pv_result.pv_forecast == pytest.approx([2.0, 0.0, 0.0])
    assert wind_pv_result.wind_forecast == pytest.approx([4.0, 0.0, 0.0])
    assert wind_pv_result.renewable_forecast == pytest.approx([6.0, 0.0, 0.0])
    assert wind_pv_result.renewable_to_bess == pytest.approx(renewable_result.renewable_to_bess)
    assert wind_pv_result.grid_import == pytest.approx(renewable_result.grid_import)
    assert wind_pv_result.total_cost == pytest.approx(renewable_result.total_cost)


def test_wind_pv_bess_dispatch_rejects_mismatched_source_lengths():
    with pytest.raises(ValueError, match="pv_forecast"):
        run_user_side_wind_pv_bess_dispatch(
            UserSideWindPVBESSDispatchInput(
                timestamps=[0, 1],
                load_forecast=[1.0, 1.0],
                pv_forecast=[1.0, 1.0, 1.0],
                wind_forecast=[1.0, 1.0],
                buy_price=[1.0, 1.0],
                price_type=["flat", "flat"],
                export=UserSidePVExportParams(allow_export=False, sell_price=0.0),
                demand_charge_rate=0.0,
                step_hours=1.0,
                bess=_bess(),
                initial_soc=0.0,
            )
        )
