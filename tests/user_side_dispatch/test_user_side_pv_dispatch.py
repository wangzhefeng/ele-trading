"""用户侧 / 园区侧光伏调度模型测试。"""

import pytest

import ele_trading.user_side_dispatch as optimization
from ele_trading.user_side_dispatch.adapters.dispatch_adapters import (
    UserSidePVDispatchInput,
    UserSidePVExportParams,
    run_user_side_pv_dispatch,
)
from ele_trading.user_side_dispatch.algorithms.user_side_renewable_dispatch_class import (
    UserSideRenewableDispatchInput,
    run_user_side_renewable_dispatch,
)


def _pv_input(
    *,
    load_forecast,
    renewable_forecast,
    allow_export=True,
    export_limit=None,
    sell_price=0.3,
    curtailment_cost_rate=0.0,
    demand_charge_rate=0.0,
):
    return UserSidePVDispatchInput(
        timestamps=list(range(len(load_forecast))),
        load_forecast=list(load_forecast),
        renewable_forecast=list(renewable_forecast),
        buy_price=[1.0] * len(load_forecast),
        price_type=["flat"] * len(load_forecast),
        export=UserSidePVExportParams(
            allow_export=allow_export,
            sell_price=sell_price,
            export_limit=export_limit,
            curtailment_cost_rate=curtailment_cost_rate,
        ),
        demand_charge_rate=demand_charge_rate,
        step_hours=1.0,
    )


def test_pv_less_than_load_is_consumed_locally():
    dispatch_input = _pv_input(load_forecast=[5.0, 6.0], renewable_forecast=[2.0, 4.0])
    result = run_user_side_pv_dispatch(dispatch_input)
    renewable_result = run_user_side_renewable_dispatch(
        UserSideRenewableDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=dispatch_input.renewable_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
        )
    )

    assert result.renewable_to_load == pytest.approx([2.0, 4.0])
    assert result.grid_import == pytest.approx([3.0, 2.0])
    assert result.renewable_to_grid == pytest.approx([0.0, 0.0])
    assert result.renewable_curtailment == pytest.approx([0.0, 0.0])
    assert result.renewable_to_load == pytest.approx(renewable_result.renewable_to_load)
    assert result.total_cost == pytest.approx(renewable_result.total_cost)
    assert result.constraint_violations == {}


def test_pv_surplus_can_export_or_curtail_by_policy():
    allow_export = run_user_side_pv_dispatch(
        _pv_input(load_forecast=[3.0], renewable_forecast=[8.0], allow_export=True)
    )
    no_export = run_user_side_pv_dispatch(
        _pv_input(load_forecast=[3.0], renewable_forecast=[8.0], allow_export=False)
    )

    assert allow_export.renewable_to_grid == pytest.approx([5.0])
    assert allow_export.renewable_curtailment == pytest.approx([0.0])
    assert no_export.renewable_to_grid == pytest.approx([0.0])
    assert no_export.renewable_curtailment == pytest.approx([5.0])


def test_export_limit_sends_excess_to_curtailment():
    result = run_user_side_pv_dispatch(
        _pv_input(
            load_forecast=[3.0],
            renewable_forecast=[10.0],
            allow_export=True,
            export_limit=4.0,
        )
    )

    assert result.renewable_to_grid == pytest.approx([4.0])
    assert result.renewable_curtailment == pytest.approx([3.0])


def test_pv_dispatch_cost_components_are_consistent():
    result = run_user_side_pv_dispatch(
        _pv_input(
            load_forecast=[5.0],
            renewable_forecast=[8.0],
            allow_export=False,
            curtailment_cost_rate=0.2,
            demand_charge_rate=10.0,
        )
    )

    assert result.energy_cost == pytest.approx(0.0)
    assert result.demand_cost == pytest.approx(0.0)
    assert result.sell_revenue == pytest.approx(0.0)
    assert result.curtailment_cost == pytest.approx(0.6)
    assert result.total_cost == pytest.approx(
        result.energy_cost
        + result.demand_cost
        + result.curtailment_cost
        - result.sell_revenue
    )


def test_pv_dispatch_rejects_invalid_input():
    with pytest.raises(ValueError, match="same length"):
        run_user_side_pv_dispatch(
            UserSidePVDispatchInput(
                timestamps=[0, 1],
                load_forecast=[1.0],
                renewable_forecast=[1.0, 1.0],
                buy_price=[1.0, 1.0],
                price_type=["flat", "flat"],
                export=UserSidePVExportParams(),
                demand_charge_rate=0.0,
                step_hours=1.0,
            )
        )
    with pytest.raises(ValueError, match="renewable_forecast must be non-negative"):
        run_user_side_pv_dispatch(
            _pv_input(load_forecast=[1.0], renewable_forecast=[-1.0])
        )
    with pytest.raises(ValueError, match="step_hours must be positive"):
        invalid = _pv_input(load_forecast=[1.0], renewable_forecast=[1.0])
        invalid.step_hours = 0.0
        run_user_side_pv_dispatch(invalid)


def test_package_exports_user_side_pv_api():
    assert optimization.UserSidePVDispatchInput is UserSidePVDispatchInput
    assert optimization.UserSidePVExportParams is UserSidePVExportParams
    assert optimization.run_user_side_pv_dispatch is run_user_side_pv_dispatch
