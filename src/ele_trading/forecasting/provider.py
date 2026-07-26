"""Forecast provider protocol (v1.3 §4).

Defines the unified interface for all forecast providers (price, load, renewable).
同一 (market/scope, horizon, issue_time) 的预测结果唯一确定（幂等），便于回测复现；
预测带 issue_time（出具时刻），优化模块据此防止使用未来信息（v1.3 §4.1）。
"""

from __future__ import annotations

import pandas as pd

from .contracts import (
    ForecastRequest,
    ForecastResult,
    _prepare_history_series,
    _valid_time_index,
)
from .registry import ForecastModelRegistry


class ForecastProvider:
    """Target-oriented provider backed by a versioned model registry."""

    def __init__(self, registry: ForecastModelRegistry) -> None:
        self.registry = registry

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Resolve and run the exact model requested for any target."""
        model = self.registry.resolve(
            request.target,
            request.model_name,
            request.model_version,
        )
        return model.forecast(request)

    def get_weather_forecast(
        self,
        request: ForecastRequest,
    ) -> ForecastResult:
        return self._forecast_typed(request, "weather")

    def get_price_forecast(
        self,
        request: ForecastRequest,
    ) -> ForecastResult:
        return self._forecast_typed(request, "price")

    def get_load_forecast(
        self,
        request: ForecastRequest,
    ) -> ForecastResult:
        return self._forecast_typed(request, "load")

    def get_wind_power_forecast(
        self,
        request: ForecastRequest,
    ) -> ForecastResult:
        return self._forecast_typed(request, "wind_power")

    def get_pv_power_forecast(
        self,
        request: ForecastRequest,
    ) -> ForecastResult:
        return self._forecast_typed(request, "pv_power")

    def _forecast_typed(
        self,
        request: ForecastRequest,
        target: str,
    ) -> ForecastResult:
        if request.target != target:
            raise ValueError(
                f"forecast request target must be {target!r}"
            )
        return self.forecast(request)


class SimpleForecastProvider(ForecastProvider):
    """Simple implementation using existing forecasters."""

    def __init__(
        self,
        price_forecaster,
        load_forecaster,
        default_history_prices: pd.Series | None = None,
    ):
        self.price_forecaster = price_forecaster
        self.load_forecaster = load_forecaster
        self.default_history_prices = default_history_prices
        registry = ForecastModelRegistry()
        if price_forecaster is not None:
            registry.register(
                "price",
                "legacy-simple",
                type(price_forecaster).__name__,
                _LegacyForecastModel(self, "price"),
                default=True,
            )
        if load_forecaster is not None:
            registry.register(
                "load",
                "legacy-simple",
                type(load_forecaster).__name__,
                _LegacyForecastModel(self, "load"),
                default=True,
            )
        super().__init__(registry)

    def _forecast_legacy(
        self,
        request: ForecastRequest,
        target: str,
    ) -> ForecastResult:
        if target == "price":
            if (
                not isinstance(self.default_history_prices, pd.Series)
                or self.default_history_prices.empty
            ):
                raise ValueError(
                    "price history must be a non-empty timezone-indexed Series"
                )
            eligible = _prepare_history_series(
                self.default_history_prices,
                issue_time=request.issue_time,
                frequency=request.frequency,
                field_name="price history",
            )
            try:
                output = self.price_forecaster.predict(
                    eligible.tolist(),
                    request.horizon,
                )
            except TypeError:
                output = self.price_forecaster.predict(request.horizon)
            return self._build_result(
                request,
                output,
                unit="CNY/MWh",
                feature_as_of=eligible.index.max(),
                quality_flags=("source:historical_price",),
            )
        load_history = getattr(self.load_forecaster, "_history", None)
        if not isinstance(load_history, pd.Series) or load_history.empty:
            raise ValueError(
                "load forecaster has no fitted historical source"
            )
        eligible = _prepare_history_series(
            load_history,
            issue_time=request.issue_time,
            frequency=request.frequency,
            field_name="load history",
        )
        feature_as_of = load_history.index.max()
        if feature_as_of > request.issue_time:
            raise ValueError(
                "load history feature_as_of is later than request.issue_time"
            )
        output = self.load_forecaster.predict(
            request.horizon,
            start_time=_valid_time_index(request)[0],
            frequency=request.frequency,
        )
        return self._build_result(
            request,
            output,
            unit="MWh/period",
            feature_as_of=eligible.index.max(),
            quality_flags=("source:fitted_load_history",),
        )

    def _build_result(
        self,
        request,
        output,
        *,
        unit: str,
        feature_as_of: pd.Timestamp,
        quality_flags: tuple[str, ...],
    ) -> ForecastResult:
        index = _valid_time_index(request)
        quantiles: dict[float, pd.Series] = {}
        if request.quantiles:
            if len(request.quantiles) != 2:
                raise ValueError(
                    "SimpleForecastProvider supports exactly two quantile levels"
                )
            quantiles = {
                request.quantiles[0]: pd.Series(
                    output.lower_quantile,
                    index=index,
                    dtype=float,
                ),
                request.quantiles[1]: pd.Series(
                    output.upper_quantile,
                    index=index,
                    dtype=float,
                ),
            }
        return ForecastResult(
            request=request,
            point=pd.Series(output.point_forecast, index=index, dtype=float),
            quantiles=quantiles,
            unit=unit,
            model_version=type(
                self.price_forecaster
                if request.target == "price"
                else self.load_forecaster
            ).__name__,
            feature_as_of=feature_as_of,
            quality_flags=quality_flags,
        )


class _LegacyForecastModel:
    """Adapter keeping the v1 reference provider on the generic path."""

    def __init__(
        self,
        provider: SimpleForecastProvider,
        target: str,
    ) -> None:
        self.provider = provider
        self.target = target

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        return self.provider._forecast_legacy(request, self.target)


def assert_no_future_info(result: ForecastResult, decision_time: pd.Timestamp) -> None:
    """预测出具时刻须不晚于决策时刻（v1.3 §4.1 无前瞻约束）。"""
    if result.request.issue_time > pd.Timestamp(decision_time):
        raise ValueError(
            f"forecast issue_time {result.request.issue_time} is after decision time {decision_time}; "
            "using future information is forbidden (v1.3 §4.1)"
        )
