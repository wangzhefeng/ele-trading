"""用户侧储能调度样例配置和模拟数据测试。"""

from pathlib import Path

from ele_trading.data_provider import (
    build_synthetic_user_side_dispatch_frame,
    build_user_side_storage_dispatch_input,
    load_user_side_storage_dispatch_config,
)
from ele_trading.optimization.user_side_storage_dispatch import (
    UserSideStorageDispatchInput,
    run_user_side_storage_dispatch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / 'configs' / 'user_side_storage_dispatch.yaml'


def test_user_side_storage_dispatch_config_can_be_loaded():
    """用户侧储能调度配置文件应可读取并包含三组参数。"""
    config = load_user_side_storage_dispatch_config(CONFIG_PATH)

    assert set(config) == {"dispatch", "storage", "synthetic_data"}
    assert config["dispatch"]["step_hours"] > 0
    assert config["storage"]["capacity"] > 0
    assert config["synthetic_data"]["periods"] > 0


def test_synthetic_user_side_dispatch_frame_shape_and_columns():
    """模拟数据应生成固定字段、固定长度且数值非负。"""
    config = load_user_side_storage_dispatch_config(CONFIG_PATH)

    df = build_synthetic_user_side_dispatch_frame(config)

    assert len(df) == config["synthetic_data"]["periods"]
    assert list(df.columns) == [
        "timestamp",
        "load_forecast",
        "buy_price",
        "price_type",
    ]
    assert df["load_forecast"].ge(0).all()
    assert df["buy_price"].ge(0).all()
    assert df["timestamp"].is_monotonic_increasing


def test_synthetic_user_side_dispatch_input_solves_without_violations():
    """模拟数据构造出的输入应可直接求解且无约束违约。"""
    config = load_user_side_storage_dispatch_config(CONFIG_PATH)

    dispatch_input = build_user_side_storage_dispatch_input(config)
    result = run_user_side_storage_dispatch(dispatch_input)

    assert isinstance(dispatch_input, UserSideStorageDispatchInput)
    assert len(result.grid_import) == config["synthetic_data"]["periods"]
    assert result.constraint_violations == {}
    assert result.total_cost >= 0
