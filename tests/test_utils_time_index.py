"""项目级时间索引工具测试。"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

import ele_trading.utils as utils
from ele_trading.utils.time_index import (
    end_of_this_es_cycle,
    es_cycle_window,
    generate_5mins,
    generate_days,
    generate_hours,
    generate_quarters,
    generate_time_points,
    process_time_index,
    start_of_this_es_cycle,
)


def test_generate_time_points_uses_left_closed_right_open_range():
    """通用时间点生成使用左闭右开区间。"""
    start = datetime(2026, 1, 1, 0, 0)
    end = datetime(2026, 1, 1, 1, 0)

    result = generate_time_points(start, end, timedelta(minutes=20))

    assert result == [
        datetime(2026, 1, 1, 0, 0),
        datetime(2026, 1, 1, 0, 20),
        datetime(2026, 1, 1, 0, 40),
    ]


def test_generate_time_point_helpers_match_expected_steps():
    """便捷函数应分别对应天、小时、15 分钟和 5 分钟步长。"""
    start = datetime(2026, 1, 1, 0, 0)

    assert generate_days(start, start + timedelta(days=2)) == [
        datetime(2026, 1, 1, 0, 0),
        datetime(2026, 1, 2, 0, 0),
    ]
    assert generate_hours(start, start + timedelta(hours=2)) == [
        datetime(2026, 1, 1, 0, 0),
        datetime(2026, 1, 1, 1, 0),
    ]
    assert generate_quarters(start, start + timedelta(minutes=45)) == [
        datetime(2026, 1, 1, 0, 0),
        datetime(2026, 1, 1, 0, 15),
        datetime(2026, 1, 1, 0, 30),
    ]
    assert generate_5mins(start, start + timedelta(minutes=15)) == [
        datetime(2026, 1, 1, 0, 0),
        datetime(2026, 1, 1, 0, 5),
        datetime(2026, 1, 1, 0, 10),
    ]


def test_es_cycle_boundaries_match_legacy_semantics():
    """储能周期边界应保持 legacy 分割点语义。"""
    before = datetime(2026, 3, 5, 21, 59)
    at_boundary = datetime(2026, 3, 5, 22, 0)
    after = datetime(2026, 3, 5, 22, 1)

    assert start_of_this_es_cycle(before, 22) == datetime(2026, 3, 4, 22, 0)
    assert end_of_this_es_cycle(before, 22) == datetime(2026, 3, 5, 22, 0)
    assert start_of_this_es_cycle(at_boundary, 22) == datetime(2026, 3, 5, 22, 0)
    assert end_of_this_es_cycle(at_boundary, 22) == datetime(2026, 3, 6, 22, 0)
    assert start_of_this_es_cycle(after, 22) == datetime(2026, 3, 5, 22, 0)
    assert end_of_this_es_cycle(after, 22) == datetime(2026, 3, 6, 22, 0)
    assert es_cycle_window(after, 22) == (
        datetime(2026, 3, 5, 22, 0),
        datetime(2026, 3, 6, 22, 0),
    )


def test_process_time_index_copies_deduplicates_and_sorts():
    """时间索引处理不修改原表，重复时间保留最后一条并排序。"""
    raw_df = pd.DataFrame(
        {
            "ts": [
                "2026-01-01 01:00:00",
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
            ],
            "value": [1, 2, 3],
        }
    )

    result = process_time_index(raw_df, "ts")

    assert "time" not in raw_df.columns
    assert result.index.tolist() == [
        pd.Timestamp("2026-01-01 00:00:00"),
        pd.Timestamp("2026-01-01 01:00:00"),
    ]
    assert result.loc[pd.Timestamp("2026-01-01 00:00:00"), "value"] == 3


def test_time_index_helpers_validate_inputs():
    """非法步长、分割小时和缺失时间列应抛出 ValueError。"""
    start = datetime(2026, 1, 1, 0, 0)

    with pytest.raises(ValueError, match="step"):
        generate_time_points(start, start + timedelta(hours=1), timedelta(0))
    with pytest.raises(ValueError, match="division_hour"):
        start_of_this_es_cycle(start, 24)
    with pytest.raises(ValueError, match="missing"):
        process_time_index(pd.DataFrame({"value": [1]}), "ts")


def test_package_time_entrypoints_are_available():
    """根 utils 入口应导出时间工具。"""
    assert utils.generate_5mins is generate_5mins
    assert utils.process_time_index is process_time_index
