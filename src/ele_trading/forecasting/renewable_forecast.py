from __future__ import annotations

from .base import ForecastOutput


class RenewableForecastStub:
    """风光预测占位器。

    当前版本不实现真实模型，只保留统一接口，方便后续接入风电、光伏和负荷预测。
    """

    def predict(self, history_values: list[float], horizon: int) -> ForecastOutput:
        if not history_values:
            raise ValueError('history_values 不能为空')
        last_value = float(history_values[-1])
        return ForecastOutput(horizon=horizon, point_forecast=[last_value] * horizon)
