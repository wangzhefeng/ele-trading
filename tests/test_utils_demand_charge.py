"""项目级需量电费工具测试。"""

import pandas as pd
import pytest

import ele_trading.utils as utils
from ele_trading.utils.demand_charge import monthly_peak_demand_cost


def test_monthly_peak_demand_cost_sums_monthly_peaks():
    """需量电费按每月最大需量求和后乘单价。"""
    load = pd.Series(
        [10.0, 20.0, 30.0, 25.0],
        index=pd.to_datetime(
            [
                "2026-01-01 00:00",
                "2026-01-02 00:00",
                "2026-02-01 00:00",
                "2026-02-02 00:00",
            ]
        ),
    )

    assert monthly_peak_demand_cost(load, 40.0) == pytest.approx((20.0 + 30.0) * 40.0)


def test_monthly_peak_demand_cost_returns_zero_for_empty_series():
    """空序列的需量电费为 0。"""
    load = pd.Series([], dtype=float, index=pd.DatetimeIndex([]))

    assert monthly_peak_demand_cost(load, 40.0) == 0.0


def test_monthly_peak_demand_cost_requires_datetime_index():
    """需量电费统计要求 DatetimeIndex。"""
    load = pd.Series([10.0, 20.0])

    with pytest.raises(ValueError, match="DatetimeIndex"):
        monthly_peak_demand_cost(load, 40.0)


def test_package_demand_charge_entrypoints_are_available():
    """根 utils 入口应导出需量电费工具。"""
    assert utils.monthly_peak_demand_cost is monthly_peak_demand_cost
