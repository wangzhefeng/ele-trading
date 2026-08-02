"""LightGBM provider tests (v4 P0 / §4.2–4.3)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.forecasting.lightgbm_provider import (
    LightGBMTradingForecastProvider,
)

TZ = "Asia/Shanghai"


def _make_history(days: int = 13) -> pd.DataFrame:
    """构造可学习的平稳合成历史：日内形态 + 工作日/周末系统性差异。

    2026-06-01 为周一；前 12 天（06-01~06-12）为训练历史，
    第 13 天（06-13，周六）作为预测真值。
    不引入趋势——LightGBM 树模型无法外推训练目标值域之外的水平。
    """
    index = pd.date_range(
        "2026-06-01 00:00", periods=96 * days, freq="15min", tz=TZ
    )
    quarter = index.hour * 4 + index.minute // 15
    daily_shape = 300.0 + 200.0 * np.sin((quarter - 20) / 96 * 2 * np.pi)
    weekend = np.asarray(index.dayofweek >= 5, dtype=float)
    # 固定种子噪声：确定性数据会让分位回归退化（带宽为零、逐点交叉）
    rng = np.random.default_rng(42)
    price = daily_shape - 80.0 * weekend + rng.normal(0.0, 5.0, len(index))
    load = (
        3.0
        + 1.0 * np.sin((quarter - 24) / 96 * 2 * np.pi)
        - 0.5 * weekend
        + rng.normal(0.0, 0.05, len(index))
    )
    return pd.DataFrame(
        {"p_real": price, "Q_real_load": load}, index=index
    )


def _request(issue_time: pd.Timestamp, target: str = "price") -> ForecastRequest:
    return ForecastRequest(
        target=target,
        scope_type="market",
        scope_id="single_settlement",
        horizon=96,
        frequency="15min",
        issue_time=issue_time,
        quantiles=(0.1, 0.9),
    )


# 训练历史 = 前 12 天；feature_as_of = 06-12 23:45（周五）
_HISTORY = _make_history().iloc[: 96 * 12]
_FEATURE_AS_OF = pd.Timestamp("2026-06-12 23:45", tz=TZ)
_ISSUE_SATURDAY = pd.Timestamp("2026-06-13 00:00", tz=TZ)


def test_lightgbm_forecast_shape_quantiles_and_version():
    provider = LightGBMTradingForecastProvider(
        _HISTORY, feature_as_of=_FEATURE_AS_OF
    )
    result = provider.forecast(_request(_ISSUE_SATURDAY))

    assert len(result.point) == 96
    assert set(result.quantiles) == {0.1, 0.9}
    # 分位近似有序：q0.1 ≤ 点预测 ≤ q0.9（允许个别点交叉）
    below = (result.quantiles[0.1] <= result.point).mean()
    above = (result.point <= result.quantiles[0.9]).mean()
    assert below >= 0.9
    assert above >= 0.9
    assert result.model_version == "lightgbm-quantile-v1"
    assert result.unit == "CNY/MWh"
    assert np.isfinite(result.point.to_numpy()).all()


def test_lightgbm_beats_seasonal_naive_on_weekend_pattern():
    """周五→周六跨预测：naive 重复周五（无周末下调），LightGBM 学习周末效应。"""
    provider = LightGBMTradingForecastProvider(
        _HISTORY, feature_as_of=_FEATURE_AS_OF
    )
    result = provider.forecast(_request(_ISSUE_SATURDAY))

    actual = _make_history().loc[
        "2026-06-13 00:00":"2026-06-13 23:45", "p_real"
    ].to_numpy(dtype=float)
    lgbm_mae = float(np.mean(np.abs(actual - result.point.to_numpy())))
    naive = _HISTORY.loc[
        "2026-06-12 00:00":"2026-06-12 23:45", "p_real"
    ].to_numpy(dtype=float)
    naive_mae = float(np.mean(np.abs(actual - naive)))

    # naive 的系统性误差 ≈ 周末下调幅度 80；LightGBM 应显著更小
    assert naive_mae > 40.0
    assert lgbm_mae < naive_mae * 0.5


def test_lightgbm_load_target_runs():
    provider = LightGBMTradingForecastProvider(
        _HISTORY, feature_as_of=_FEATURE_AS_OF
    )
    result = provider.forecast(_request(_ISSUE_SATURDAY, target="load"))
    assert len(result.point) == 96
    assert result.unit == "MWh/period"


def test_lightgbm_rejects_lookahead_history():
    provider = LightGBMTradingForecastProvider(
        _HISTORY, feature_as_of=_FEATURE_AS_OF
    )
    with pytest.raises(ValueError, match="newer than"):
        provider.forecast(
            _request(pd.Timestamp("2026-06-05 00:00", tz=TZ))
        )


def test_lightgbm_rejects_short_history():
    history = _make_history(days=5)
    provider = LightGBMTradingForecastProvider(
        history, feature_as_of=history.index.max()
    )
    # issue_time 在 feature_as_of 之后，确保先触发历史长度校验
    with pytest.raises(ValueError, match="requires"):
        provider.forecast(
            _request(pd.Timestamp("2026-06-06 00:00", tz=TZ))
        )


def test_lightgbm_rejects_unsupported_target():
    provider = LightGBMTradingForecastProvider(
        _HISTORY, feature_as_of=_FEATURE_AS_OF
    )
    with pytest.raises(ValueError, match="supports"):
        provider.forecast(
            _request(_ISSUE_SATURDAY, target="wind_power")
        )
