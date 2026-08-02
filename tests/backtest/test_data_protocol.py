"""回测数据切分契约测试（v4 P0 / §8.2.2）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.backtest.data_protocol import (
    DataSplit,
    assert_forecast_vintage,
    assert_no_lookahead,
    split_data,
)
from ele_trading.backtest.metrics import (
    deviation_penalty_share,
    price_capture_ratio,
    quantile_calibration_error,
)

TZ = "Asia/Shanghai"
INDEX = pd.date_range(
    "2026-08-01 00:00", periods=96 * 30, freq="15min", tz=TZ
)
DATA = pd.DataFrame(
    {"p_real": np.arange(len(INDEX), dtype=float), "load": 1.0},
    index=INDEX,
)

DAY = pd.Timedelta(days=1)


def test_split_three_way_is_disjoint_and_ordered():
    split = split_data(
        DATA,
        train_end=pd.Timestamp("2026-08-10 00:00", tz=TZ),
        validation_end=pd.Timestamp("2026-08-20 00:00", tz=TZ),
        test_end=pd.Timestamp("2026-08-30 23:45", tz=TZ),
    )
    assert split.train.index.max() < split.validation.index.min()
    assert split.validation.index.max() < split.test.index.min()
    assert len(split.train.index.intersection(split.test.index)) == 0
    assert_no_lookahead(split)


def test_split_two_way_without_validation():
    split = split_data(
        DATA,
        train_end=pd.Timestamp("2026-08-15 00:00", tz=TZ),
        test_end=pd.Timestamp("2026-08-30 23:45", tz=TZ),
    )
    assert split.validation is None
    assert split.train.index.max() < split.test.index.min()
    assert_no_lookahead(split)


def test_split_rejects_bad_boundaries():
    with pytest.raises(ValueError, match="train_end < validation_end"):
        split_data(
            DATA,
            train_end=pd.Timestamp("2026-08-20 00:00", tz=TZ),
            validation_end=pd.Timestamp("2026-08-10 00:00", tz=TZ),
            test_end=pd.Timestamp("2026-08-30 23:45", tz=TZ),
        )
    with pytest.raises(ValueError, match="within the data range"):
        split_data(
            DATA,
            train_end=pd.Timestamp("2026-09-05 00:00", tz=TZ),
            test_end=pd.Timestamp("2026-09-30 23:45", tz=TZ),
        )


def test_assert_no_lookahead_detects_train_test_overlap():
    split = split_data(
        DATA,
        train_end=pd.Timestamp("2026-08-10 00:00", tz=TZ),
        test_end=pd.Timestamp("2026-08-20 23:45", tz=TZ),
    )
    # 模拟泄漏：把测试行塞进训练集
    leaked = split.train.copy()
    leaked = pd.concat(
        [leaked, split.test.iloc[:5]]
    ).sort_index()
    mutated = DataSplit(
        train=leaked,
        validation=None,
        test=split.test,
        train_start=split.train_start,
        train_end=split.train_end,
        validation_start=None,
        validation_end=None,
        test_start=split.test_start,
        test_end=split.test_end,
    )
    with pytest.raises(AssertionError, match="overlap|strictly earlier"):
        assert_no_lookahead(mutated)


def test_assert_forecast_vintage_ok():
    decision = pd.Timestamp("2026-08-20 00:00", tz=TZ)
    assert_forecast_vintage(
        [decision - DAY, decision - pd.Timedelta(hours=2)],
        [decision, decision + pd.Timedelta(hours=1)],
    )


def test_assert_forecast_vintage_detects_lookahead():
    decision = pd.Timestamp("2026-08-20 00:00", tz=TZ)
    with pytest.raises(AssertionError, match="after decision"):
        assert_forecast_vintage(
            [decision + DAY],  # 预测出具晚于决策 → 未来信息
            [decision],
        )


def test_assert_forecast_vintage_requires_alignment():
    with pytest.raises(ValueError, match="aligned"):
        assert_forecast_vintage(
            [pd.Timestamp("2026-08-20 00:00", tz=TZ)],
            [],
        )


# ---------------- 指标（v4 §8.3） ----------------

def test_price_capture_ratio():
    assert price_capture_ratio(80.0, 100.0) == pytest.approx(0.8)
    assert price_capture_ratio(100.0, 0.0) == 0.0
    assert price_capture_ratio(-10.0, 100.0) == pytest.approx(-0.1)


def test_deviation_penalty_share():
    assert deviation_penalty_share(20.0, 100.0) == pytest.approx(0.2)
    assert deviation_penalty_share(20.0, 0.0) == 0.0


def test_quantile_calibration_error():
    actual = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    # q0.5 覆盖 1,2,3 → 覆盖率 0.6 → 误差 0.1
    q50 = np.full(5, 3.5)
    assert quantile_calibration_error(
        actual, q50, quantile=0.5
    ) == pytest.approx(0.1)
    # 完美校准分位 → 误差 0
    q40 = np.full(5, 2.5)
    assert quantile_calibration_error(
        actual, q40, quantile=0.4
    ) == pytest.approx(0.0)
    with pytest.raises(ValueError, match="within"):
        quantile_calibration_error(actual, q50, quantile=1.2)
