"""用户侧储能调度样例配置和模拟数据测试。"""

from pathlib import Path

from ele_trading.data_provider import (
    build_synthetic_user_side_bess_dispatch_frame,
    build_user_side_bess_dispatch_input,
)
from ele_trading.optimization.user_side_bess_dispatch import (
    UserSideBESSDispatchInput,
    run_user_side_bess_dispatch,
)
from ele_trading.utils.io import read_yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / 'configs' / 'user_side_bess_dispatch.yaml'


def test_user_side_bess_dispatch_config_can_be_loaded():
    """用户侧储能调度配置文件应可读取并包含三组参数。"""
    config = read_yaml(CONFIG_PATH)

    assert set(config) == {"dispatch", "bess", "synthetic_data"}
    assert config["dispatch"]["step_hours"] > 0
    assert config["bess"]["capacity"] > 0
    assert config["synthetic_data"]["periods"] > 0


def test_synthetic_user_side_dispatch_frame_shape_and_columns():
    """模拟数据应生成固定字段、固定长度且数值非负。"""
    config = read_yaml(CONFIG_PATH)

    df = build_synthetic_user_side_bess_dispatch_frame(config)

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
    config = read_yaml(CONFIG_PATH)

    dispatch_input = build_user_side_bess_dispatch_input(config)
    result = run_user_side_bess_dispatch(dispatch_input)

    assert isinstance(dispatch_input, UserSideBESSDispatchInput)
    assert len(result.grid_import) == config["synthetic_data"]["periods"]
    assert result.constraint_violations == {}
    assert result.total_cost >= 0
