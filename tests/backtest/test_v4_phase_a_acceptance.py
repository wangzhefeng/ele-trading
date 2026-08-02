"""v4 Phase A 验收测试（§9.2）。

- walk-forward 下 LightGBM 价格预测 pinball loss 低于 seasonal-naive；
- 性能基准：96 点 + 20 场景两阶段求解 ≤ 30s；LightGBM 训练 + 分位
  预测（单日 96 点）≤ 60s（slow 标记，默认跳过，`-m slow` 显式跑）。
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.forecasting.lightgbm_provider import (
    LightGBMTradingForecastProvider,
)
from ele_trading.forecasting.metrics import pinball_loss
from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.optimization.bess_model import BESSConfig
from ele_trading.optimization.solver import SolveStatus
from ele_trading.optimization.two_stage_cvar import solve_two_stage_cvar
from ele_trading.scenario.contracts import Scenario, ScenarioSet

TZ = "Asia/Shanghai"


def _synthetic_history(days: int) -> pd.DataFrame:
    """确定性合成历史：日内形态 + 周末下调（LightGBM 可学习，naive 不能）。"""
    index = pd.date_range(
        "2026-05-25 00:00", periods=96 * days, freq="15min", tz=TZ
    )
    quarter = index.hour * 4 + index.minute // 15
    shape = 300.0 + 200.0 * np.sin((quarter - 20) / 96 * 2 * np.pi)
    weekend = np.asarray(index.dayofweek >= 5, dtype=float)
    rng = np.random.default_rng(11)
    price = shape - 80.0 * weekend + rng.normal(0.0, 5.0, len(index))
    return pd.DataFrame({"p_real": price}, index=index)


# 2026-05-25 周一；walk-forward 测试日：06-05（周五）~ 06-08（周一）
_TEST_DAYS = [
    pd.Timestamp("2026-06-05 00:00", tz=TZ),
    pd.Timestamp("2026-06-06 00:00", tz=TZ),
    pd.Timestamp("2026-06-07 00:00", tz=TZ),
    pd.Timestamp("2026-06-08 00:00", tz=TZ),
]


def _day_slice(data: pd.DataFrame, day: pd.Timestamp) -> np.ndarray:
    start = day
    end = day + pd.Timedelta(hours=23, minutes=45)
    return data.loc[start:end, "p_real"].to_numpy(dtype=float)


def _walk_forward_pinball(model: str) -> float:
    """walk-forward：逐日训练（≤ 决策日历史）→ 预测 → pinball 聚合。"""
    total_loss = 0.0
    n_points = 0
    for day in _TEST_DAYS:
        history = _synthetic_history(
            int((day - pd.Timestamp("2026-05-25", tz=TZ)).days) + 1
        ).iloc[:-96]
        feature_as_of = history.index.max()
        request = ForecastRequest(
            target="price",
            scope_type="market",
            scope_id="single_settlement",
            horizon=96,
            frequency="15min",
            issue_time=day,
            quantiles=(0.1, 0.9),  # naive 只支持双分位；点预测取中位数
        )
        if model == "lightgbm":
            provider = LightGBMTradingForecastProvider(
                history, feature_as_of=feature_as_of
            )
        else:
            provider = SeasonalNaiveTradingForecastProvider(
                history, feature_as_of=feature_as_of
            )
        result = provider.forecast(request)
        actual = _day_slice(
            _synthetic_history(15), day
        )  # 真值来自完整生成过程
        metric = pinball_loss(
            actual,
            result.point.to_numpy(dtype=float),
            quantile=0.5,
            unit="CNY/MWh",
            grain="15min",
        )
        total_loss += metric.value * len(actual)
        n_points += len(actual)
    return total_loss / n_points


def test_walk_forward_lightgbm_beats_seasonal_naive_pinball():
    """Phase A：walk-forward pinball loss 低于 seasonal-naive 基线。"""
    naive_loss = _walk_forward_pinball("seasonal_naive")
    lgbm_loss = _walk_forward_pinball("lightgbm")
    # 周末效应使 naive 系统性偏离；LightGBM 学习周末标志应显著更优
    assert lgbm_loss < naive_loss * 0.6


@pytest.mark.slow
def test_perf_two_stage_96pt_20scenarios_under_30s():
    """Phase A 性能基准：96 点 + 20 场景两阶段求解 ≤ 30s。"""
    rng = np.random.default_rng(5)
    index = pd.date_range(
        "2026-08-01 00:15", periods=96, freq="15min", tz=TZ
    )
    scenarios = []
    probabilities = {}
    for i in range(20):
        price = 400.0 + 100.0 * np.sin(np.arange(96) / 96 * 2 * np.pi)
        price = price + rng.normal(0.0, 30.0, 96)
        scenarios.append(
            Scenario(
                scenario_id=f"s{i}",
                probability=1.0 / 20,
                issue_time=pd.Timestamp("2026-08-01 00:00", tz=TZ),
                trajectories={
                    "price": pd.Series(price, index=index),
                    "load": pd.Series(np.full(96, 3.0), index=index),
                    "wind": pd.Series(np.full(96, 1.0), index=index),
                    "pv": pd.Series(np.full(96, 0.5), index=index),
                },
                seed=5,
                source_versions={
                    "price": "v1",
                    "load": "v1",
                    "wind": "v1",
                    "pv": "v1",
                },
            )
        )
        probabilities[i] = 1.0 / 20
    scenario_set = ScenarioSet(
        horizon=96,
        valid_time_index=index,
        units={
            "price": "CNY/MWh",
            "load": "MWh/period",
            "wind": "MWh/period",
            "pv": "MWh/period",
        },
        scenarios=tuple(scenarios),
    )
    started = time.perf_counter()
    result = solve_two_stage_cvar(
        scenario_set,
        bess_config=BESSConfig(
            soc0=5.0,
            soc_min=1.0,
            soc_max=10.0,
            p_ch_max=3.0,
            p_dis_max=3.0,
            eta_ch=0.95,
            eta_dis=0.95,
            dt=0.25,
        ),
        deviation_penalty_positive=1.0,
        deviation_penalty_negative=1.0,
    )
    elapsed = time.perf_counter() - started
    assert result.solve_status is SolveStatus.OPTIMAL, result.solve_status
    assert result.objective_value is not None
    assert elapsed <= 30.0, f"two-stage solve took {elapsed:.1f}s (>30s)"


@pytest.mark.slow
def test_perf_lightgbm_train_and_quantile_under_60s():
    """Phase A 性能基准：LightGBM 训练 + 分位预测（单日 96 点）≤ 60s。"""
    history = _synthetic_history(30)
    request = ForecastRequest(
        target="price",
        scope_type="market",
        scope_id="single_settlement",
        horizon=96,
        frequency="15min",
        issue_time=pd.Timestamp("2026-06-24 00:00", tz=TZ),
        quantiles=(0.1, 0.9),
    )
    provider = LightGBMTradingForecastProvider(
        history, feature_as_of=history.index.max()
    )
    started = time.perf_counter()
    provider.forecast(request)  # 首次调用含全部训练
    elapsed = time.perf_counter() - started
    assert elapsed <= 60.0, f"lightgbm train+predict took {elapsed:.1f}s (>60s)"
