"""用户侧光伏 / 光伏+储能模拟数据测试。"""

from ele_trading.data_provider.todo import (
    build_synthetic_user_side_pv_dispatch_frame,
    build_user_side_pv_dispatch_input,
    build_user_side_pv_bess_dispatch_input,
)
from ele_trading.optimization.todo.user_side_pv_dispatch import (
    UserSidePVDispatchInput,
    run_user_side_pv_dispatch,
)
from ele_trading.optimization.todo.user_side_pv_bess_dispatch import (
    UserSidePVBESSDispatchInput,
    run_user_side_pv_bess_dispatch,
)


def _config():
    return {
        "dispatch": {
            "demand_charge_rate": 20.0,
            "step_hours": 1.0,
            "cycle_cost_rate": 0.01,
            "terminal_soc_target": 4.0,
        },
        "bess": {
            "capacity": 10.0,
            "soc_min": 1.0,
            "soc_max": 10.0,
            "p_ch_max": 4.0,
            "p_dis_max": 4.0,
            "eta_ch": 0.95,
            "eta_dis": 0.95,
            "initial_soc": 4.0,
        },
        "export": {
            "allow_export": True,
            "sell_price": 0.3,
            "export_limit": 5.0,
            "curtailment_cost_rate": 0.0,
        },
        "synthetic_data": {
            "start_time": "2026-01-01 00:00:00",
            "periods": 24,
            "freq_minutes": 60,
            "base_load": 5.0,
            "midday_peak_load": 3.0,
            "evening_peak_load": 6.0,
            "pv_peak_power": 9.0,
            "valley_price": 0.28,
            "flat_price": 0.62,
            "peak_price": 1.05,
            "price_type_periods": [
                {"type": "valley", "start_hour": 0, "end_hour": 7},
                {"type": "flat", "start_hour": 7, "end_hour": 17},
                {"type": "peak", "start_hour": 17, "end_hour": 22},
                {"type": "flat", "start_hour": 22, "end_hour": 24},
            ],
        },
    }


def test_synthetic_pv_frame_shape_and_columns():
    frame = build_synthetic_user_side_pv_dispatch_frame(_config())

    assert len(frame) == 24
    assert list(frame.columns) == [
        "timestamp",
        "load_forecast",
        "pv_forecast",
        "buy_price",
        "price_type",
    ]
    assert frame["load_forecast"].ge(0).all()
    assert frame["pv_forecast"].ge(0).all()
    assert frame["buy_price"].ge(0).all()
    assert frame["timestamp"].is_monotonic_increasing


def test_synthetic_pv_inputs_solve_without_violations():
    config = _config()
    pv_input = build_user_side_pv_dispatch_input(config)
    pv_bess_input = build_user_side_pv_bess_dispatch_input(config)

    pv_result = run_user_side_pv_dispatch(pv_input)
    pv_bess_result = run_user_side_pv_bess_dispatch(pv_bess_input)

    assert isinstance(pv_input, UserSidePVDispatchInput)
    assert isinstance(pv_bess_input, UserSidePVBESSDispatchInput)
    assert pv_result.constraint_violations == {}
    assert pv_bess_result.constraint_violations == {}
