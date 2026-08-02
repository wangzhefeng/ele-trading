"""Scenario diagnostics tests (v4 P0 / §5.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.scenario.contracts import Scenario, ScenarioSet
from ele_trading.scenario.diagnostics import (
    assert_reproducible,
    diagnose_scenario_set,
)

TZ = "Asia/Shanghai"
ISSUE = pd.Timestamp("2026-07-01 00:00", tz=TZ)
INDEX = pd.date_range("2026-07-01 00:15", periods=96, freq="15min", tz=TZ)
_HORIZON = 96


def _base_profiles() -> tuple[np.ndarray, np.ndarray]:
    """96 步平滑基线（日内正弦形态），保证 pooled 分位良态。"""
    quarter = np.arange(_HORIZON)
    price = 400.0 + 200.0 * np.sin((quarter - 20) / _HORIZON * 2 * np.pi)
    load = 3.0 + 1.0 * np.sin((quarter - 24) / _HORIZON * 2 * np.pi)
    return price, load


def _scenario_set(
    price_offset: float = 0.0,
    *,
    extreme_tail: bool = False,
) -> ScenarioSet:
    """构造 96 步、price/load 双目标的场景集。"""
    base_price, base_load = _base_profiles()
    rng = np.random.default_rng(7)
    scenarios = []
    for i in range(6):
        price = base_price + price_offset + rng.normal(0.0, 5.0, _HORIZON)
        if extreme_tail and i == 0:
            price = price + 500.0  # 注入极端场景
        scenarios.append(
            Scenario(
                scenario_id=f"s{i}",
                probability=1.0 / 6,
                issue_time=ISSUE,
                trajectories={
                    "price": pd.Series(price, index=INDEX),
                    "load": pd.Series(
                        base_load + rng.normal(0.0, 0.05, _HORIZON),
                        index=INDEX,
                    ),
                },
                seed=7,
                source_versions={"price": "v1", "load": "v1"},
            )
        )
    return ScenarioSet(
        horizon=_HORIZON,
        valid_time_index=INDEX,
        units={"price": "CNY/MWh", "load": "MWh/period"},
        scenarios=tuple(scenarios),
    )


_BASE_PRICE, _BASE_LOAD = _base_profiles()
_REFERENCE = {
    "price": pd.Series(_BASE_PRICE, index=INDEX),
    "load": pd.Series(_BASE_LOAD, index=INDEX),
}


def test_diagnostics_pass_on_consistent_set():
    report = diagnose_scenario_set(_scenario_set(), reference=_REFERENCE)
    assert report.passed
    names = [c.name for c in report.checks]
    assert names == [
        "weight_conservation",
        "marginal_mean_consistency",
        "marginal_quantile_consistency",
        "correlation_preservation",
        "extreme_coverage",
    ]
    # 无历史参考时后两项显式 skipped
    assert "skipped" in report.checks[3].detail


def test_diagnostics_detect_systematic_shift():
    """场景整体偏移点预测 → 边际一致性失败（均值漂移 >5%）。"""
    shifted = _scenario_set(price_offset=100.0)
    report = diagnose_scenario_set(shifted, reference=_REFERENCE)
    assert not report.passed
    assert "marginal_mean_consistency" in report.failed_checks


def _historical_reference() -> pd.DataFrame:
    """历史参考：与场景同源形态（正弦基线 + 大噪声），price/load 相关。"""
    rng = np.random.default_rng(3)
    base_price, base_load = _base_profiles()
    repeats = 20  # 20 天
    price = np.tile(base_price, repeats) + rng.normal(
        0.0, 30.0, _HORIZON * repeats
    )
    load = np.tile(base_load, repeats) + rng.normal(
        0.0, 0.2, _HORIZON * repeats
    )
    return pd.DataFrame({"price": price, "load": load})


def test_diagnostics_with_historical_reference():
    historical = _historical_reference()
    report = diagnose_scenario_set(
        _scenario_set(extreme_tail=True),
        reference=_REFERENCE,
        historical=historical,
    )
    corr_check = next(
        c for c in report.checks if c.name == "correlation_preservation"
    )
    extreme_check = next(
        c for c in report.checks if c.name == "extreme_coverage"
    )
    # 历史与场景同受正弦基线驱动 → price/load 相关结构一致
    assert corr_check.passed
    # 注入了 +500 的极端场景 → 超过历史 q95 的覆盖应达标
    assert extreme_check.passed
    assert extreme_check.value is not None and extreme_check.value > 0.0


def test_diagnostics_detect_missing_extreme_coverage():
    historical = _historical_reference()
    # 无极端场景注入：历史噪声 σ=30 的 q95 高于场景最大水平 → 覆盖不足
    report = diagnose_scenario_set(
        _scenario_set(),
        reference=_REFERENCE,
        historical=historical,
    )
    extreme_check = next(
        c for c in report.checks if c.name == "extreme_coverage"
    )
    assert not extreme_check.passed


def test_reference_must_cover_all_targets():
    with pytest.raises(ValueError, match="missing"):
        diagnose_scenario_set(
            _scenario_set(),
            reference={"price": _REFERENCE["price"]},
        )


def test_assert_reproducible_accepts_deterministic_builder():
    calls = {"n": 0}

    def builder() -> ScenarioSet:
        calls["n"] += 1
        return _scenario_set()

    assert_reproducible(builder)
    assert calls["n"] == 2


def test_assert_reproducible_rejects_nondeterministic_builder():
    def builder() -> ScenarioSet:
        # 无固定 seed → 每次不同
        base_price, base_load = _base_profiles()
        scenarios = (
            Scenario(
                scenario_id="s0",
                probability=1.0,
                issue_time=ISSUE,
                trajectories={
                    "price": pd.Series(
                        base_price + np.random.normal(0, 1, _HORIZON),
                        index=INDEX,
                    ),
                    "load": pd.Series(base_load, index=INDEX),
                },
                seed=1,
                source_versions={"price": "v1", "load": "v1"},
            ),
        )
        return ScenarioSet(
            horizon=_HORIZON,
            valid_time_index=INDEX,
            units={"price": "CNY/MWh", "load": "MWh/period"},
            scenarios=scenarios,
        )

    with pytest.raises(AssertionError, match="not reproducible"):
        assert_reproducible(builder)
