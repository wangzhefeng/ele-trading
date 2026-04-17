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
