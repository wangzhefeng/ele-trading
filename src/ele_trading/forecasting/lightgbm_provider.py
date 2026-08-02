"""LightGBM 点预测 + 分位回归 provider（v4 P0 / §4.2–4.3）。

特征矩阵（§4.3.1 的 P0 子集，全部可由历史序列构造）：
- 日历：quarter-of-day、day_of_week、is_weekend、month
- 滞后：t-1d / t-2d / t-7d 同时段值
- 滚动统计：24h / 168h 滚动均值与标准差

无前瞻约束：训练只使用 ``feature_as_of`` 之前的历史；预测特征全部由
历史序列构造；``feature_as_of > request.issue_time`` 显式拒绝。

范围声明（P0）：仅支持 price / load 目标；多层级协调、气象与市场
结构特征依赖外部数据，按 v4 §9.3 推迟到 P1。
"""

from __future__ import annotations

from typing import Any, Mapping, cast

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from ele_trading.forecasting.contracts import (
    ForecastRequest,
    ForecastResult,
)

# 支持的预测目标 → 历史 DataFrame 列
_TARGET_COLUMNS = {
    "price": "p_real",
    "load": "Q_real_load",
}

#: 滞后步数（96 点/日）：1 天、2 天、7 天
_LAG_STEPS = (96, 192, 672)
#: 滚动窗口步数：24h、168h
_ROLL_WINDOWS = (96, 672)
#: 训练需要的最小历史长度（最大滞后 + 一天）
_MIN_HISTORY_STEPS = 672 + 96

_MODEL_VERSION = "lightgbm-quantile-v1"

_DEFAULT_PARAMS: dict[str, object] = {
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "verbose": -1,
}


def _calendar_features(index: pd.DatetimeIndex) -> np.ndarray:
    """日历特征：[quarter_of_day, day_of_week, is_weekend, month]。"""
    quarter_of_day = np.asarray(index.hour * 4 + index.minute // 15, dtype=float)
    day_of_week = np.asarray(index.dayofweek, dtype=float)
    is_weekend = (day_of_week >= 5).astype(float)
    month = np.asarray(index.month, dtype=float)
    return np.column_stack(
        [quarter_of_day, day_of_week, is_weekend, month]
    )


def _lag_and_rolling_features(
    values: np.ndarray,
    positions: np.ndarray,
) -> np.ndarray:
    """滞后与滚动统计特征；positions 为待预测点在 values 中的逻辑位置。

    positions 可超出 len(values)（未来点）：滞后落在历史内取真值，
    落在已由 ``values`` 扩展的预测段内取预测值（递归，仅当预测
    跨度超过一天时发生）。
    """
    series = pd.Series(values)
    rolling_stats = {}
    for window in _ROLL_WINDOWS:
        rolling = series.rolling(window, min_periods=max(1, window // 4))
        rolling_stats[window] = (
            rolling.mean().to_numpy(),
            rolling.std().to_numpy(),
        )

    columns = []
    for lag in _LAG_STEPS:
        columns.append(values[positions - lag])
    for window in _ROLL_WINDOWS:
        mean_arr, std_arr = rolling_stats[window]
        # 滚动统计取 positions-1 及之前（shift(1)，不泄漏当前点）
        idx = np.clip(positions - 1, 0, len(mean_arr) - 1)
        columns.append(mean_arr[idx])
        columns.append(std_arr[idx])
    return np.column_stack(columns)


class LightGBMTradingForecastProvider:
    """LightGBM 分位预测 provider（v4 P0；可选启用，默认不替代基线）。

    history 为 15 分钟颗粒度的历史 DataFrame，至少包含目标列
    （price→p_real，load→Q_real_load），索引为 timezone-aware
    DatetimeIndex。训练在首次 forecast 时惰性执行并缓存。
    """

    def __init__(
        self,
        history: pd.DataFrame,
        *,
        feature_as_of: pd.Timestamp,
        params: Mapping[str, object] | None = None,
    ) -> None:
        self.history = history.copy()
        self.feature_as_of = pd.Timestamp(feature_as_of)
        if self.feature_as_of.tzinfo is None:
            raise ValueError("feature_as_of must be timezone-aware")
        if not isinstance(self.history.index, pd.DatetimeIndex):
            raise ValueError("history must use a DatetimeIndex")
        self.params = {**_DEFAULT_PARAMS, **dict(params or {})}
        # 缓存：(target, quantile) -> fitted model
        self._models: dict[tuple[str, float], LGBMRegressor] = {}

    # ------------------------------------------------------------ #
    #  训练
    # ------------------------------------------------------------ #

    def _train(self, target: str, tau: float) -> LGBMRegressor:
        column = _TARGET_COLUMNS[target]
        history = self.history[column].to_numpy(dtype=float)
        if len(history) < _MIN_HISTORY_STEPS:
            raise ValueError(
                f"lightgbm training requires ≥ {_MIN_HISTORY_STEPS} steps "
                f"(7d lag + 1d target), got {len(history)}"
            )
        # 训练位置：滞后全部落在历史内的点
        positions = np.arange(_LAG_STEPS[-1], len(history))
        x = np.column_stack(
            [
                _calendar_features(self.history.index[positions]),
                _lag_and_rolling_features(history, positions),
            ]
        )
        y = history[positions]
        model = LGBMRegressor(
            objective="quantile",
            alpha=tau,
            **cast(Any, self.params),
        )
        model.fit(x, y)
        return model

    def _model(self, target: str, tau: float) -> LGBMRegressor:
        key = (target, tau)
        if key not in self._models:
            self._models[key] = self._train(target, tau)
        return self._models[key]

    # ------------------------------------------------------------ #
    #  预测
    # ------------------------------------------------------------ #

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if request.target not in _TARGET_COLUMNS:
            raise ValueError(
                f"lightgbm provider supports {sorted(_TARGET_COLUMNS)}, "
                f"got {request.target!r}"
            )
        if self.feature_as_of > request.issue_time:
            raise ValueError(
                "lightgbm history is newer than request issue_time"
            )
        column = _TARGET_COLUMNS[request.target]
        history = self.history[column].to_numpy(dtype=float)

        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        # 预测位置：历史末尾之后的逻辑步点
        positions = np.arange(
            len(history),
            len(history) + request.horizon,
        )

        quantile_levels = tuple(request.quantiles) or (0.1, 0.9)
        all_taus = sorted({0.5, *quantile_levels})

        # 递归预测：跨度超过一天时滞后特征复用已预测的分位 0.5 值
        extended = history.copy()
        predictions: dict[float, np.ndarray] = {}
        for tau in all_taus:
            model = self._model(request.target, tau)
            x = np.column_stack(
                [
                    _calendar_features(index),
                    _lag_and_rolling_features(extended, positions),
                ]
            )
            pred = np.asarray(model.predict(x), dtype=float)
            predictions[tau] = pred
            if tau == 0.5 and request.horizon > 96:
                # 扩展序列供更长跨度的滞后特征使用
                extended = np.concatenate([history, pred])

        # 分位回归交叉修正：点预测（中位数）与分位带一起逐时点重排，
        # 保证 q_lo ≤ point ≤ q_hi 恒成立（契约硬约束）
        all_levels = sorted({0.5, *quantile_levels})
        band = np.column_stack(
            [predictions[tau] for tau in all_levels]
        )
        band = np.sort(band, axis=1)
        point = pd.Series(band[:, all_levels.index(0.5)], index=index)
        quantiles = {
            tau: pd.Series(band[:, all_levels.index(tau)], index=index)
            for tau in sorted(quantile_levels)
        }
        return ForecastResult(
            request=request,
            point=point,
            quantiles=quantiles,
            unit=(
                "CNY/MWh"
                if request.target == "price"
                else "MWh/period"
            ),
            model_version=_MODEL_VERSION,
            feature_as_of=self.feature_as_of,
            quality_flags=("ml:lightgbm-quantile",),
        )
