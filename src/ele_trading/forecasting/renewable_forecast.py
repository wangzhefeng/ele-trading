from __future__ import annotations

from .base import ForecastOutput


class RenewableForecaster:
    """统一可再生出力预测包装器。

    当前版本先支持两种最基础输入：
    - 已预计算 profile 的直接截取
    - 历史序列 persistence 外推
    """

    def predict(self, history_values: list[float], horizon: int) -> ForecastOutput:
        if not history_values:
            raise ValueError("history_values 不能为空")
        last_value = float(history_values[-1])
        return ForecastOutput(horizon=horizon, point_forecast=[last_value] * horizon)

    def predict_from_profile(self, profile_values: list[float], horizon: int) -> ForecastOutput:
        if not profile_values:
            raise ValueError("profile_values 不能为空")
        if horizon <= 0:
            raise ValueError("horizon must be positive")
        point = [float(value) for value in profile_values[:horizon]]
        if len(point) < horizon:
            point.extend([point[-1]] * (horizon - len(point)))
        return ForecastOutput(horizon=horizon, point_forecast=point)


class RenewableForecastStub(RenewableForecaster):
    """兼容旧名称。"""
