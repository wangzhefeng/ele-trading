"""项目级时间序列对齐工具测试。"""

from pathlib import Path

import pandas as pd
import pytest

import ele_trading.utils as utils
from ele_trading.utils.data_alignment import ensure_datetime_index, read_time_value_csv


def test_ensure_datetime_index_uses_time_column_without_mutating_source():
    """存在时间列时，应返回 DatetimeIndex 副本且不修改原始 DataFrame。"""
    df = pd.DataFrame(
        {
            "Time": ["2026-01-01 01:00", "2026-01-01 00:00"],
            "value": [1.0, 2.0],
        }
    )

    result = ensure_datetime_index(df, "Time")

    assert "Time" in df.columns
    assert isinstance(result.index, pd.DatetimeIndex)
    assert result.index.tolist() == [
        pd.Timestamp("2026-01-01 00:00"),
        pd.Timestamp("2026-01-01 01:00"),
    ]
    assert result["value"].tolist() == [2.0, 1.0]


def test_ensure_datetime_index_accepts_existing_datetime_index():
    """已有 DatetimeIndex 时，应只排序并返回副本。"""
    df = pd.DataFrame(
        {"value": [1.0, 2.0]},
        index=pd.to_datetime(["2026-01-01 01:00", "2026-01-01 00:00"]),
    )

    result = ensure_datetime_index(df)

    assert result.index.tolist() == [
        pd.Timestamp("2026-01-01 00:00"),
        pd.Timestamp("2026-01-01 01:00"),
    ]
    assert result["value"].tolist() == [2.0, 1.0]


def test_ensure_datetime_index_requires_time_axis():
    """缺少时间列且 index 不是 DatetimeIndex 时抛出 ValueError。"""
    with pytest.raises(ValueError, match="Time"):
        ensure_datetime_index(pd.DataFrame({"value": [1.0]}), "Time")


def test_read_time_value_csv_filters_sorts_and_parses_numeric(tmp_path: Path):
    """CSV 读取工具应按时间过滤、排序并解析数值列。"""
    path = tmp_path / "series.csv"
    pd.DataFrame(
        {
            "time": ["2026-01-01 02:00", "2026-01-01 00:00", "2026-01-01 01:00"],
            "value": ["3.0", "1.0", "2.0"],
        }
    ).to_csv(path, index=False)

    result = read_time_value_csv(
        path,
        pd.Timestamp("2026-01-01 01:00"),
        pd.Timestamp("2026-01-01 03:00"),
    )

    assert result.index.tolist() == [
        pd.Timestamp("2026-01-01 01:00"),
        pd.Timestamp("2026-01-01 02:00"),
    ]
    assert result.tolist() == [2.0, 3.0]


def test_read_time_value_csv_raises_for_bad_numeric_value(tmp_path: Path):
    """数值列不可解析时应抛出错误，而不是静默填 0。"""
    path = tmp_path / "series.csv"
    pd.DataFrame({"time": ["2026-01-01 00:00"], "value": ["bad"]}).to_csv(path, index=False)

    with pytest.raises(ValueError):
        read_time_value_csv(path, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02"))


def test_package_data_alignment_entrypoints_are_available():
    """根 utils 入口应导出时间序列工具。"""
    assert utils.ensure_datetime_index is ensure_datetime_index
    assert utils.read_time_value_csv is read_time_value_csv
