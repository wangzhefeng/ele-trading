"""项目级电价处理工具测试。"""

import pandas as pd
import pytest

import utils
from src.es_rolling_schedule.Utils.ele_price_process import (
    flat_valley_price_diff as legacy_flat_valley_price_diff,
)
from utils.energy_price import flat_valley_price_diff, flatten_valley_price_diff


def test_flatten_valley_price_diff_does_not_mutate_by_default():
    """默认返回副本，不修改原始 DataFrame。"""
    df = pd.DataFrame(
        {
            "eleType": ["谷", "平", "深谷"],
            "elePrice": [0.2, 0.5, 0.1],
        },
        index=pd.to_datetime(
            ["2026-01-01 01:00", "2026-01-01 02:00", "2026-01-01 03:00"]
        ),
    )

    result = flatten_valley_price_diff(df)

    assert df["elePrice"].tolist() == [0.2, 0.5, 0.1]
    assert result["elePrice"].tolist() == [0.1, 0.5, 0.1]


def test_flatten_valley_price_diff_uses_last_valley_like_price():
    """同时存在谷和深谷时，使用最后出现的谷类时段价格统一价格。"""
    df = pd.DataFrame(
        {
            "eleType": ["深谷", "平", "谷", "峰"],
            "elePrice": [0.1, 0.5, 0.25, 0.9],
        },
        index=pd.to_datetime(
            [
                "2026-01-01 01:00",
                "2026-01-01 02:00",
                "2026-01-01 03:00",
                "2026-01-01 04:00",
            ]
        ),
    )

    result = flatten_valley_price_diff(df)

    assert result.loc[result["eleType"].isin(["谷", "深谷"]), "elePrice"].tolist() == [
        0.25,
        0.25,
    ]


def test_flatten_valley_price_diff_handles_single_valley_type():
    """只存在一种谷类时段时，也按该类型最后价格统一。"""
    df = pd.DataFrame(
        {
            "eleType": ["谷", "平", "谷"],
            "elePrice": [0.2, 0.5, 0.3],
        },
        index=pd.to_datetime(
            ["2026-01-01 01:00", "2026-01-01 02:00", "2026-01-01 03:00"]
        ),
    )

    result = flatten_valley_price_diff(df)

    assert result["elePrice"].tolist() == [0.3, 0.5, 0.3]


def test_flatten_valley_price_diff_keeps_frame_without_valley_type():
    """不存在谷类时段时，返回内容不变。"""
    df = pd.DataFrame({"eleType": ["平", "峰"], "elePrice": [0.5, 0.9]})

    result = flatten_valley_price_diff(df)

    pd.testing.assert_frame_equal(result, df)
    assert result is not df


def test_flatten_valley_price_diff_requires_price_and_type_columns():
    """缺少价格列或类型列时抛出 ValueError。"""
    with pytest.raises(ValueError, match="elePrice"):
        flatten_valley_price_diff(pd.DataFrame({"eleType": ["谷"]}))
    with pytest.raises(ValueError, match="eleType"):
        flatten_valley_price_diff(pd.DataFrame({"elePrice": [0.2]}))


def test_legacy_flat_valley_price_diff_mutates_in_place():
    """legacy 兼容函数保持原地修改行为。"""
    df = pd.DataFrame({"eleType": ["谷", "深谷"], "elePrice": [0.2, 0.1]})

    result = flat_valley_price_diff(df)

    assert result is df
    assert df["elePrice"].tolist() == [0.1, 0.1]


def test_legacy_and_package_price_entrypoints_are_available():
    """根 utils 入口和 legacy 路径都应导出电价工具。"""
    assert utils.flatten_valley_price_diff is flatten_valley_price_diff
    assert utils.flat_valley_price_diff is flat_valley_price_diff
    assert legacy_flat_valley_price_diff is flat_valley_price_diff
