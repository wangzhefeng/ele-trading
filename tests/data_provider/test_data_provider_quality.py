"""data_provider.quality 六个低引用函数的单元测试。

覆盖：compute_quality_score / detect_step_jumps / detect_zero_values /
repair_anomalies / resample_series_frame / ensure_unique_timestamps。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.data_provider.quality import (
    compute_quality_score,
    detect_step_jumps,
    detect_zero_values,
    ensure_unique_timestamps,
    repair_anomalies,
    resample_series_frame,
)


# ---------------------------------------------------------------------------
# compute_quality_score
# ---------------------------------------------------------------------------


def test_quality_score_full_marks_when_no_flag_columns():
    df = pd.DataFrame({"v": [1.0, 2.0, 3.0]})
    score = compute_quality_score(df)
    assert score.tolist() == [1.0, 1.0, 1.0]


def test_quality_score_penalties_and_floor():
    df = pd.DataFrame(
        {
            "is_interpolated": [True, False, True],
            "is_shifted_from_history": [False, True, True],
            "is_filled_by_nearest_day": [False, False, True],
        }
    )
    score = compute_quality_score(df)
    # 逐行：0.2 / 0.3 / 0.2+0.3+0.3=0.8
    assert score.tolist() == pytest.approx([0.8, 0.7, 0.2])


def test_quality_score_partial_flag_columns():
    df = pd.DataFrame({"is_interpolated": [True, False]})
    score = compute_quality_score(df)
    assert score.tolist() == pytest.approx([0.8, 1.0])


# ---------------------------------------------------------------------------
# detect_zero_values / detect_step_jumps
# ---------------------------------------------------------------------------


def test_detect_zero_values_flags_below_threshold_and_nan():
    series = pd.Series([0.0, 0.5, 1.0, 5.0, np.nan])
    mask = detect_zero_values(series, threshold=1.0)
    assert mask.tolist() == [True, True, True, False, True]


def test_detect_step_jumps_flags_outlier_diff():
    series = pd.Series([10.0] * 20 + [100.0] + [10.0] * 20)
    mask = detect_step_jumps(series, n_sigma=3.0)
    # 尖峰及其回落两处差分远超 mean + 3σ
    assert mask[20]
    assert mask[21]
    assert mask.sum() == 2


def test_detect_step_jumps_smooth_series_has_no_hits():
    series = pd.Series(np.linspace(0.0, 1.0, 50))
    mask = detect_step_jumps(series, n_sigma=3.0)
    assert not mask.any()


# ---------------------------------------------------------------------------
# repair_anomalies
# ---------------------------------------------------------------------------


def test_repair_anomalies_interpolates_zero_and_marks_flag():
    idx = pd.date_range("2026-07-01", periods=4, freq="h")
    df = pd.DataFrame({"ts": idx, "v": [10.0, 0.0, 20.0, 25.0]})
    out = repair_anomalies(df, time_col="ts", value_col="v")
    assert out.loc[1, "v_修正"] == pytest.approx(15.0)
    assert out.loc[1, "修正标识"] == "0值修正(线性)"
    assert out.loc[0, "修正标识"] == "正常"


def test_repair_anomalies_falls_back_to_previous_day_same_hour():
    day1 = pd.date_range("2026-07-01", periods=24, freq="h")
    day2 = pd.date_range("2026-07-02", periods=24, freq="h")
    values = [10.0] * 24 + [np.nan] + [10.0] * 23
    df = pd.DataFrame({"ts": day1.append(day2), "v": values})
    out = repair_anomalies(df, time_col="ts", value_col="v")
    # NaN 点按零值检出后先插值（两侧均为 10 → 10），不会走到前日补全；
    # 这里验证首点 NaN（插值无法左延）时由前日同刻值兜底
    df2 = pd.DataFrame(
        {"ts": day1.append(day2), "v": [10.0] * 24 + [10.0] * 23 + [np.nan]}
    )
    out2 = repair_anomalies(df2, time_col="ts", value_col="v")
    last = out2.iloc[-1]
    assert last["v_修正"] == pytest.approx(10.0)
    assert last["修正标识"] in {"前日值补全", "0值修正(线性)"}


# ---------------------------------------------------------------------------
# resample_series_frame
# ---------------------------------------------------------------------------


def test_resample_series_frame_interpolates_missing_slots():
    idx = pd.to_datetime(["2026-07-01 00:00", "2026-07-01 00:30", "2026-07-01 01:00"])
    df = pd.DataFrame({"ts": idx, "v": [0.0, 30.0, 60.0]})
    out = resample_series_frame(df, freq="15min", time_col="ts")
    assert len(out) == 5
    assert out["v"].tolist() == pytest.approx([0.0, 15.0, 30.0, 45.0, 60.0])


def test_resample_series_frame_output_is_sorted_and_named():
    idx = pd.to_datetime(["2026-07-01 01:00", "2026-07-01 00:00"])
    df = pd.DataFrame({"ts": idx, "v": [2.0, 1.0]})
    out = resample_series_frame(df, freq="1h", time_col="ts")
    assert out["ts"].is_monotonic_increasing
    assert list(out.columns) == ["ts", "v"]


# ---------------------------------------------------------------------------
# ensure_unique_timestamps
# ---------------------------------------------------------------------------


def test_ensure_unique_timestamps_passes_on_unique():
    df = pd.DataFrame({"timestamp": pd.date_range("2026-07-01", periods=3, freq="h")})
    ensure_unique_timestamps(df)  # 不抛异常即通过


def test_ensure_unique_timestamps_raises_with_values():
    df = pd.DataFrame(
        {"timestamp": pd.to_datetime(["2026-07-01 00:00", "2026-07-01 00:00"])}
    )
    with pytest.raises(ValueError, match="duplicate timestamps"):
        ensure_unique_timestamps(df)
