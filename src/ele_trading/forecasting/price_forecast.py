from __future__ import annotations

import numpy as np

from .base import ForecastOutput


class SimplePriceForecaster:
    """基于历史均值和波动的占位式价格预测器。"""

    def predict(self, history_prices: list[float], horizon: int) -> ForecastOutput:
        if not history_prices:
            raise ValueError('history_prices 不能为空')

        history = np.asarray(history_prices, dtype=float)
        mean_price = float(history.mean())
        std_price = float(history.std()) if len(history) > 1 else 0.0

        # 研究型原型阶段先输出平坦预测，并给出简单分位区间。
        point = [mean_price for _ in range(horizon)]
        lower = [max(0.0, mean_price - std_price) for _ in range(horizon)]
        upper = [mean_price + std_price for _ in range(horizon)]
        return ForecastOutput(horizon=horizon, point_forecast=point, lower_quantile=lower, upper_quantile=upper)


class ARIMAForecaster:
    """基于 ARIMA 模型的价格预测器。

    使用 statsmodels ARIMA 进行拟合和预测，输出点预测及 95% 置信区间。

    参数
    ----
    order : tuple[int, int, int]
        ARIMA 阶数 (p, d, q)，默认 (2, 0, 1)。
    """

    def __init__(self, order: tuple[int, int, int] = (2, 0, 1)) -> None:
        self._order = order
        self._fitted_model = None

    def fit(self, history_prices: list[float]) -> None:
        """拟合 ARIMA 模型。"""
        if not history_prices:
            raise ValueError('history_prices 不能为空')
        from statsmodels.tsa.arima.model import ARIMA
        history = np.asarray(history_prices, dtype=float)
        model = ARIMA(history, order=self._order)
        self._fitted_model = model.fit()

    def predict(self, horizon: int) -> ForecastOutput:
        """预测未来 horizon 步。"""
        if self._fitted_model is None:
            raise RuntimeError('Call fit() before predict()')
        forecast = self._fitted_model.get_forecast(steps=horizon)
        mean = forecast.predicted_mean
        ci = forecast.conf_int(alpha=0.05)
        point = [max(0.0, float(v)) for v in mean]
        lower = [max(0.0, float(v)) for v in ci[:, 0]]
        upper = [max(0.0, float(v)) for v in ci[:, 1]]
        return ForecastOutput(
            horizon=horizon,
            point_forecast=point,
            lower_quantile=lower,
            upper_quantile=upper,
        )
