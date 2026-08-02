"""真实数据切分契约（v4 P0 / §8.2.2）。

walk-forward 回测的数据切片约定：

- 训练集 [t0, t1]：模型训练；
- 验证集 [t1, t2]（可选）：超参选择；
- 测试集 [t2, t3]：回测评估（严格无前瞻）。

无前瞻由两层校验保护：

1. ``split_data`` 只产出不相交、单调的切片；
2. ``assert_no_lookahead`` 显式复核切片不重叠/不泄漏，并校验
   forecast vintage：每条预测的 issue_time 不得晚于对应决策时刻
   （v1.3 §4.1 / v4 §8.2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True, slots=True)
class DataSplit:
    """回测数据切分（train / validation(可选) / test）。"""

    train: pd.DataFrame
    validation: pd.DataFrame | None
    test: pd.DataFrame
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    validation_start: pd.Timestamp | None
    validation_end: pd.Timestamp | None
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def _require_tz_index(data: pd.DataFrame) -> pd.DatetimeIndex:
    index = data.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError(
            "data must use a timezone-aware DatetimeIndex"
        )
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("data index must be unique and monotonic")
    return index


def split_data(
    data: pd.DataFrame,
    *,
    train_end: pd.Timestamp,
    validation_end: pd.Timestamp | None = None,
    test_end: pd.Timestamp,
) -> DataSplit:
    """按时间边界切分数据，产出不相交的 train/validation/test。

    边界语义：``(train_start, train_end]``、``(train_end,
    validation_end]``（若有）、``(validation_end, test_end]``；
    边界必须单调递增且全部落在数据范围内。
    """
    index = _require_tz_index(data)
    train_end = pd.Timestamp(train_end)
    validation_end = (
        pd.Timestamp(validation_end) if validation_end is not None else None
    )
    test_end = pd.Timestamp(test_end)

    if validation_end is not None:
        if not (train_end < validation_end < test_end):
            raise ValueError(
                "split boundaries must satisfy "
                "train_end < validation_end < test_end"
            )
    elif not (train_end < test_end):
        raise ValueError("split boundaries must satisfy train_end < test_end")

    if index[0] > train_end or index[-1] < test_end:
        raise ValueError("split boundaries must fall within the data range")

    train = data.loc[:train_end]
    if validation_end is None:
        validation: pd.DataFrame | None = None
        validation_start = validation_end = None
        test = data.loc[train_end:].iloc[1:]  # 排除 train_end 重复点
    else:
        validation = data.loc[train_end:].iloc[1:]
        validation = validation.loc[:validation_end]
        validation_start = (
            validation.index[0] if not validation.empty else None
        )
        test = data.loc[validation_end:].iloc[1:]
    test = test.loc[:test_end]

    if train.empty or test.empty:
        raise ValueError("split produced an empty train or test slice")
    if validation is not None and validation.empty:
        raise ValueError("split produced an empty validation slice")

    return DataSplit(
        train=train,
        validation=validation,
        test=test,
        train_start=pd.Timestamp(index[0]),
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        test_start=pd.Timestamp(test.index[0]),
        test_end=test_end,
    )


def assert_no_lookahead(split: DataSplit) -> None:
    """复核切片不相交、有序，训练数据不含任何测试段行。"""
    if not split.train.index.is_monotonic_increasing:
        raise AssertionError("train slice index is not monotonic")
    if not split.test.index.is_monotonic_increasing:
        raise AssertionError("test slice index is not monotonic")
    if not split.train.index.is_unique or not split.test.index.is_unique:
        raise AssertionError("train/test slices contain duplicate timestamps")
    if len(split.train.index.intersection(split.test.index)) > 0:
        raise AssertionError(
            "train and test slices overlap (lookahead leak)"
        )
    if split.train.index.max() >= split.test.index.min():
        raise AssertionError(
            "train end must be strictly earlier than test start"
        )
    if split.validation is not None:
        if (
            len(split.train.index.intersection(split.validation.index)) > 0
            or len(split.test.index.intersection(split.validation.index)) > 0
        ):
            raise AssertionError("validation slice overlaps train or test")
        if not (
            split.train.index.max()
            < split.validation.index.min()
            <= split.validation.index.max()
            < split.test.index.min()
        ):
            raise AssertionError("slice ordering violated")


def assert_forecast_vintage(
    issue_times: Iterable[pd.Timestamp],
    decision_times: Iterable[pd.Timestamp],
) -> None:
    """校验 forecast vintage：每条预测的 issue_time ≤ 对应决策时刻。

    测试集中预测必须按 issue_time 严格对齐：任何预测出具时刻晚于
    决策时刻即构成使用未来信息（v4 §8.2.2 / v1.3 §4.1）。
    """
    issues = [pd.Timestamp(item) for item in issue_times]
    decisions = [pd.Timestamp(item) for item in decision_times]
    if len(issues) != len(decisions):
        raise ValueError(
            "issue_times and decision_times must be pairwise aligned"
        )
    for issue, decision in zip(issues, decisions, strict=True):
        if issue > decision:
            raise AssertionError(
                f"forecast issue_time {issue} is after decision time "
                f"{decision}; lookahead forbidden (v4 §8.2.2)"
            )
