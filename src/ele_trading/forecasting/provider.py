"""Forecast provider protocol (v1.3 §4).

Defines the unified interface for all forecast providers (price, load, renewable).
同一 (market/scope, horizon, issue_time) 的预测结果唯一确定（幂等），便于回测复现；
预测带 issue_time（出具时刻），优化模块据此防止使用未来信息（v1.3 §4.1）。
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from ele_trading.trading.contracts import ForecastResult


class ForecastProvider(Protocol):
    """Unified forecast provider interface."""

    def get_price_forecast(
        self, market: str, horizon: int | str, *, quantiles: bool = False
    ) -> ForecastResult:
        """Get price forecast.

        Args:
            market: "dayah" | "real" | "long"
            horizon: Number of periods or "monthly"
            quantiles: Whether to return quantile bands

        Returns:
            ForecastResult with point forecast and optional quantiles
        """
        ...

    def get_load_forecast(
        self, scope: str, horizon: int | str, *, quantiles: bool = False
    ) -> ForecastResult:
        """Get load forecast.

        Args:
            scope: "dayah_open" | "real_open" | "long"
            horizon: Number of periods or "monthly"
            quantiles: Whether to return quantile bands

        Returns:
            ForecastResult with point forecast and optional quantiles
        """
        ...


class SimpleForecastProvider:
    """Simple implementation using existing forecasters."""

    def __init__(
        self,
        price_forecaster,
        load_forecaster,
        default_history_prices: list[float] | None = None,
        issue_time: pd.Timestamp | None = None,
    ):
        self.price_forecaster = price_forecaster
        self.load_forecaster = load_forecaster
        # Default history for SimplePriceForecaster (flat 300 元/MWh)
        self.default_history_prices = default_history_prices or [300.0] * 96
        # 显式 issue_time 便于回测复现（v1.3 §4.1 幂等）；缺省取当前时刻
        self.issue_time = issue_time

    def _resolve_issue_time(self, issue_time: pd.Timestamp | None) -> pd.Timestamp:
        resolved = issue_time or self.issue_time or pd.Timestamp.now()
        return pd.Timestamp(resolved)

    def get_price_forecast(
        self,
        market: str,
        horizon: int | str,
        *,
        quantiles: bool = False,
        issue_time: pd.Timestamp | None = None,
    ) -> ForecastResult:
        """Get price forecast using price_forecaster."""
        if isinstance(horizon, str):
            horizon = 12 if horizon == "monthly" else 96

        # SimplePriceForecaster needs history_prices, ARIMAForecaster only needs horizon
        try:
            output = self.price_forecaster.predict(self.default_history_prices, horizon)
        except TypeError:
            output = self.price_forecaster.predict(horizon)

        return ForecastResult(
            name=f"p_{market}_pre",
            unit="元/MWh",
            freq_minutes=15 if horizon > 24 else 0,
            issue_time=self._resolve_issue_time(issue_time),
            point=pd.Series(output.point_forecast),
            lower=pd.Series(output.lower_quantile) if quantiles else None,
            upper=pd.Series(output.upper_quantile) if quantiles else None,
            quantile_level=0.90 if quantiles else None,
        )

    def get_load_forecast(
        self,
        scope: str,
        horizon: int | str,
        *,
        quantiles: bool = False,
        issue_time: pd.Timestamp | None = None,
    ) -> ForecastResult:
        """Get load forecast using load_forecaster."""
        if isinstance(horizon, str):
            horizon = 12 if horizon == "monthly" else 96

        output = self.load_forecaster.predict(horizon)
        return ForecastResult(
            name=f"Q_{scope}_pre",
            unit="MWh/刻",
            freq_minutes=15 if horizon > 24 else 0,
            issue_time=self._resolve_issue_time(issue_time),
            point=pd.Series(output.point_forecast),
            lower=pd.Series(output.lower_quantile) if quantiles else None,
            upper=pd.Series(output.upper_quantile) if quantiles else None,
            quantile_level=0.90 if quantiles else None,
        )


def assert_no_future_info(result: ForecastResult, decision_time: pd.Timestamp) -> None:
    """预测出具时刻须不晚于决策时刻（v1.3 §4.1 无前瞻约束）。"""
    if result.issue_time > pd.Timestamp(decision_time):
        raise ValueError(
            f"forecast issue_time {result.issue_time} is after decision time {decision_time}; "
            "using future information is forbidden (v1.3 §4.1)"
        )
