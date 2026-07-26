from __future__ import annotations

from statistics import NormalDist
from typing import Mapping

import numpy as np
import pandas as pd
from pandas.tseries.frequencies import to_offset

from .base import ForecastOutput
from .contracts import (
    ForecastRequest,
    ForecastResult,
    _prepare_history_series,
    _valid_time_index,
)


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


class ARIMAForecastModel:
    """Request-oriented adapter for the compatible ARIMA forecaster."""

    def __init__(
        self,
        *,
        history: pd.Series,
        order: tuple[int, int, int] = (2, 0, 1),
        market_scope: str,
    ) -> None:
        if not market_scope.strip():
            raise ValueError("market_scope must not be empty")
        if (
            not isinstance(history, pd.Series)
            or not isinstance(history.index, pd.DatetimeIndex)
            or history.index.tz is None
            or history.empty
        ):
            raise ValueError(
                "ARIMA history must be a non-empty timezone-indexed Series"
            )
        if history.index.has_duplicates:
            raise ValueError(
                "ARIMA history must not contain duplicate timestamps"
            )
        if not history.index.is_monotonic_increasing:
            raise ValueError(
                "ARIMA history timestamps must be monotonic increasing"
            )
        values = history.astype(float)
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError("ARIMA history must contain finite values")
        if len(values) >= 3 and pd.infer_freq(values.index) is None:
            raise ValueError("ARIMA history time axis must be regular")

        self.history = values.copy()
        self.market_scope = market_scope
        self.order = order
        self.model_version = (
            f"price-arima-{order[0]}-{order[1]}-{order[2]}-v1"
        )
        self._forecaster = ARIMAForecaster(order=order)
        self._forecaster.fit(values.tolist())

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if request.target != "price":
            raise ValueError("ARIMA model requires target 'price'")
        if request.data.get("market_scope") != self.market_scope:
            raise ValueError(
                "request market_scope does not match fitted ARIMA scope"
            )
        feature_as_of = self.history.index.max()
        if feature_as_of > request.issue_time:
            raise ValueError(
                "ARIMA history feature_as_of is later than request.issue_time"
            )
        _prepare_history_series(
            self.history,
            issue_time=request.issue_time,
            frequency=request.frequency,
            field_name="ARIMA history",
        )

        forecast = self._forecaster._fitted_model.get_forecast(
            steps=request.horizon
        )
        point_values = np.maximum(
            0.0,
            np.asarray(forecast.predicted_mean, dtype=float),
        )
        standard_error = np.asarray(forecast.se_mean, dtype=float)
        valid_times = _valid_time_index(request)
        point = pd.Series(
            point_values,
            index=valid_times,
            dtype=float,
        )
        quantiles = {
            level: pd.Series(
                np.maximum(
                    0.0,
                    point_values
                    + NormalDist().inv_cdf(level) * standard_error,
                ),
                index=valid_times,
                dtype=float,
            )
            for level in request.quantiles
        }
        return ForecastResult(
            request=request,
            point=point,
            quantiles=quantiles,
            unit="CNY/MWh",
            model_version=self.model_version,
            feature_as_of=feature_as_of,
            quality_flags=(
                "source:historical_price",
                "model:arima",
            ),
        )


class PriceForecastModel:
    """Request-oriented seasonal-naive or regression price baseline."""

    _SCOPES = {
        "day_ahead_reference",
        "real_time_reference",
        "mid_long_term",
    }

    def __init__(
        self,
        *,
        history_by_scope: Mapping[str, pd.Series],
        method: str = "seasonal_naive",
    ) -> None:
        if method not in {"seasonal_naive", "regression"}:
            raise ValueError(
                "price method must be seasonal_naive or regression"
            )
        self.history_by_scope = dict(history_by_scope)
        self.method = method

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if request.target != "price":
            raise ValueError("price model requires target 'price'")
        market_scope = request.data.get("market_scope")
        if market_scope not in self._SCOPES:
            raise ValueError(
                "request data market_scope must be day_ahead_reference, "
                "real_time_reference, or mid_long_term"
            )
        history = self.history_by_scope.get(str(market_scope))
        if history is None:
            raise ValueError(
                f"price history is missing for market_scope {market_scope!r}"
            )
        eligible = _prepare_history_series(
            history,
            issue_time=request.issue_time,
            frequency=request.frequency,
            field_name="price history",
        )

        frequency_kind = self._frequency_kind(request.frequency)
        valid_times = _valid_time_index(request)
        flags = [f"baseline:{self.method}", f"price_scope:{market_scope}"]
        if self.method == "seasonal_naive":
            point_values: list[float] = []
            for valid_time in valid_times:
                if frequency_kind == "15min":
                    matching = eligible.loc[
                        (eligible.index.hour == valid_time.hour)
                        & (eligible.index.minute == valid_time.minute)
                    ]
                else:
                    matching = eligible.loc[
                        eligible.index.month == valid_time.month
                    ]
                if matching.empty:
                    matching = eligible.iloc[[-1]]
                    if "degraded:missing_seasonal_slot" not in flags:
                        flags.append("degraded:missing_seasonal_slot")
                point_values.append(float(matching.iloc[-1]))
            point = np.asarray(point_values, dtype=float)
            seasonal_period = 96 if frequency_kind == "15min" else 12
            if len(eligible) < seasonal_period:
                flags.append("degraded:insufficient_history")
            residual_scale = self._seasonal_residual_scale(
                eligible,
                seasonal_period,
            )
            model_version = "price-seasonal-naive-v1"
        else:
            if len(eligible) < 2:
                raise ValueError(
                    "price regression requires at least two historical values"
                )
            x = np.arange(len(eligible), dtype=float)
            slope, intercept = np.polyfit(
                x,
                eligible.to_numpy(dtype=float),
                deg=1,
            )
            future_x = np.arange(
                len(eligible),
                len(eligible) + request.horizon,
                dtype=float,
            )
            point = intercept + slope * future_x
            residuals = eligible.to_numpy(dtype=float) - (
                intercept + slope * x
            )
            residual_scale = float(np.std(residuals))
            model_version = "price-regression-v1"

        point_series = pd.Series(point, index=valid_times, dtype=float)
        quantiles = {
            level: pd.Series(
                point + NormalDist().inv_cdf(level) * residual_scale,
                index=valid_times,
                dtype=float,
            )
            for level in request.quantiles
        }
        return ForecastResult(
            request=request,
            point=point_series,
            quantiles=quantiles,
            unit="CNY/MWh",
            model_version=model_version,
            feature_as_of=eligible.index.max(),
            quality_flags=tuple(flags),
        )

    @staticmethod
    def _frequency_kind(frequency: str) -> str:
        offset = to_offset(frequency)
        if isinstance(offset, pd.offsets.Tick):
            if offset.nanos == pd.Timedelta(minutes=15).value:
                return "15min"
        if isinstance(offset, (pd.offsets.MonthBegin, pd.offsets.MonthEnd)):
            return "monthly"
        raise ValueError(
            "price forecasts support only 15-minute and monthly frequencies"
        )

    @staticmethod
    def _seasonal_residual_scale(
        history: pd.Series,
        seasonal_period: int,
    ) -> float:
        if len(history) > seasonal_period:
            residuals = (
                history.iloc[seasonal_period:].to_numpy(dtype=float)
                - history.iloc[:-seasonal_period].to_numpy(dtype=float)
            )
            return float(np.std(residuals))
        return float(np.std(history.to_numpy(dtype=float)))
