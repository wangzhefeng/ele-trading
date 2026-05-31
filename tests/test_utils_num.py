"""项目级数值工具测试。"""

import pytest

import ele_trading.utils as utils
from ele_trading.utils.num_utils import inclusive_float_range


def test_inclusive_float_range_includes_right_endpoint():
    """浮点扫描序列应包含右端点。"""
    assert inclusive_float_range(0.0, 1.0, 0.5) == [0.0, 0.5, 1.0]


def test_inclusive_float_range_handles_float_error():
    """浮点累加误差不应丢失理论上的右端点。"""
    assert inclusive_float_range(0.0, 0.3, 0.1, ndigits=6) == [0.0, 0.1, 0.2, 0.3]


def test_inclusive_float_range_appends_non_step_aligned_hi():
    """右端点不在步长网格上时仍应追加 hi。"""
    assert inclusive_float_range(0.0, 1.0, 0.4, ndigits=6) == [0.0, 0.4, 0.8, 1.0]


def test_inclusive_float_range_requires_positive_step():
    """步长必须为正数。"""
    with pytest.raises(ValueError, match="step"):
        inclusive_float_range(0.0, 1.0, 0.0)


def test_package_num_entrypoints_are_available():
    """根 utils 入口应导出数值工具。"""
    assert utils.inclusive_float_range is inclusive_float_range
