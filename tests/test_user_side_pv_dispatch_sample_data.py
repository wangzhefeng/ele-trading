"""用户侧光伏调度样例配置和模拟数据测试。"""

from pathlib import Path

from ele_trading.data_provider import (
    build_synthetic_user_side_pv_dispatch_frame,
    build_user_side_pv_dispatch_input,
    load_user_side_pv_dispatch_config,
)
from ele_trading.optimization.user_side_pv_dispatch import (
    UserSidePVDispatchInput,
    run_user_side_pv_dispatch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / 'configs' / 'user_side_pv_dispatch.yaml'


def test_user_side_pv_dispatch_config_can_be_loaded():
    config = load_user_side_pv_dispatch_config(CONFIG_PATH)

    assert set(config) == {"dispatch", "export", "synthetic_data"}
    assert config["dispatch"]["step_hours"] > 0
    assert config["synthetic_data"]["periods"] > 0


def test_synthetic_user_side_pv_dispatch_frame_shape_and_columns():
    config = load_user_side_pv_dispatch_config(CONFIG_PATH)

    frame = build_synthetic_user_side_pv_dispatch_frame(config)

    assert len(frame) == config["synthetic_data"]["periods"]
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


def test_synthetic_user_side_pv_dispatch_input_solves_without_violations():
    config = load_user_side_pv_dispatch_config(CONFIG_PATH)

    dispatch_input = build_user_side_pv_dispatch_input(config)
    result = run_user_side_pv_dispatch(dispatch_input)

    assert isinstance(dispatch_input, UserSidePVDispatchInput)
    assert len(result.grid_import) == config["synthetic_data"]["periods"]
    assert result.constraint_violations == {}
