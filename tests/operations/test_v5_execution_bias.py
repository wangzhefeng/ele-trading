"""v5 V5-3（§11.3）：执行偏差学习与约束收紧。"""

from __future__ import annotations

import pytest

from ele_trading.operations.execution_bias import (
    ExecutionBiasEstimator,
)


def test_persistent_discharge_shortfall_tightens_power_limits():
    estimator = ExecutionBiasEstimator(window=16, tightening_sigma=2.0)
    for _ in range(8):
        estimator.record_power(
            planned_mw=2.0,
            actual_mw=1.8,
        )

    tightening = estimator.constraint_tightening()
    assert tightening.power_derate_mw == pytest.approx(0.2, abs=1e-9)
    assert tightening.sample_count == 8
    assert tightening.available


def test_no_bias_means_no_tightening():
    estimator = ExecutionBiasEstimator(window=16, tightening_sigma=2.0)
    for _ in range(8):
        estimator.record_power(planned_mw=1.5, actual_mw=1.5)

    tightening = estimator.constraint_tightening()
    assert tightening.power_derate_mw == pytest.approx(0.0, abs=1e-9)


def test_cold_start_reports_unavailable_without_tightening():
    estimator = ExecutionBiasEstimator(window=16, min_samples=4)
    estimator.record_power(planned_mw=2.0, actual_mw=1.0)

    tightening = estimator.constraint_tightening()
    assert not tightening.available
    assert tightening.power_derate_mw == 0.0
    assert tightening.soc_reserve_mwh == 0.0


def test_soc_bias_builds_reserve_and_window_limits_memory():
    estimator = ExecutionBiasEstimator(window=4, min_samples=2)
    for actual in (1.90, 1.85, 1.80, 1.75, 1.70):
        estimator.record_soc(planned_mwh=2.0, actual_mwh=actual)

    tightening = estimator.constraint_tightening()
    # 窗口只保留最近 4 条：偏差均值 -(0.15+0.20+0.25+0.30)/4
    assert tightening.soc_reserve_mwh == pytest.approx(0.225, abs=1e-9)
    assert tightening.available


def test_overdelivery_does_not_create_negative_derate():
    estimator = ExecutionBiasEstimator(window=8, min_samples=2)
    for _ in range(3):
        estimator.record_power(planned_mw=1.0, actual_mw=1.2)

    tightening = estimator.constraint_tightening()
    assert tightening.power_derate_mw == 0.0


def test_invalid_inputs_raise():
    estimator = ExecutionBiasEstimator(window=8)
    with pytest.raises(ValueError, match="finite"):
        estimator.record_power(planned_mw=float("nan"), actual_mw=1.0)
    with pytest.raises(ValueError, match="finite"):
        estimator.record_soc(planned_mwh=1.0, actual_mwh=float("inf"))
    with pytest.raises(ValueError, match="window"):
        ExecutionBiasEstimator(window=0)
    with pytest.raises(ValueError, match="min_samples"):
        ExecutionBiasEstimator(window=8, min_samples=0)
