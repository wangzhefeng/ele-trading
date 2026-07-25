"""Unit tests for DR allocator and forecast provider."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.forecasting.load_forecast import LoadForecaster
from ele_trading.forecasting.price_forecast import SimplePriceForecaster
from ele_trading.forecasting.provider import SimpleForecastProvider
from ele_trading.trading.contracts import MarketConfig
from ele_trading.trading.dr_allocator import evaluate_dr_participation


@pytest.fixture
def config():
    return MarketConfig()


class TestDRAllocator:
    def test_participate_when_compensation_high(self, config):
        """Should participate when DR compensation > opportunity cost."""
        adjustable = np.full(96, 5.0)  # 5 MW available
        dr_compensation = 2000.0  # 2000 元/MWh (削峰上限)
        window = (40, 60)  # peak hours

        decision = evaluate_dr_participation(adjustable, dr_compensation, window, config)
        assert decision.participate
        assert decision.response_qty > 0
        assert decision.expected_compensation > decision.arbitrage_opportunity_cost

    def test_reject_when_compensation_low(self, config):
        """Should reject when DR compensation < opportunity cost."""
        adjustable = np.full(96, 5.0)
        dr_compensation = 10.0  # very low
        window = (40, 60)

        decision = evaluate_dr_participation(adjustable, dr_compensation, window, config)
        assert not decision.participate
        assert decision.reject_reason is not None


class TestLoadForecaster:
    def test_fit_predict(self):
        """Load forecaster should fit and predict."""
        # Generate synthetic load data
        dates = pd.date_range("2026-01-01", periods=30 * 24, freq="h")
        load = 100 + 20 * np.sin(2 * np.pi * dates.hour / 24) + np.random.normal(0, 5, len(dates))
        load_series = pd.Series(load, index=dates)

        forecaster = LoadForecaster(ar_lags=24)
        forecaster.fit(load_series)
        output = forecaster.predict(horizon=96)

        assert len(output.point_forecast) == 96
        assert all(p >= 0 for p in output.point_forecast)
        assert len(output.lower_quantile) == 96
        assert len(output.upper_quantile) == 96


class TestForecastProvider:
    def test_price_forecast(self):
        """Provider should return valid price forecast."""
        price_forecaster = SimplePriceForecaster()
        load_forecaster = LoadForecaster()
        provider = SimpleForecastProvider(price_forecaster, load_forecaster)

        result = provider.get_price_forecast("dayah", horizon=96, quantiles=True)
        assert result.name == "p_dayah_pre"
        assert result.unit == "元/MWh"
        assert result.freq_minutes == 15
        assert len(result.point) == 96
        assert result.lower is not None
        assert result.upper is not None
        assert all(result.lower <= result.point)
        assert all(result.point <= result.upper)

    def test_load_forecast(self):
        """Provider should return valid load forecast."""
        price_forecaster = SimplePriceForecaster()
        load_forecaster = LoadForecaster()

        # Fit load forecaster
        dates = pd.date_range("2026-01-01", periods=30 * 24, freq="h")
        load = 100 + 20 * np.sin(2 * np.pi * dates.hour / 24)
        load_series = pd.Series(load, index=dates)
        load_forecaster.fit(load_series)

        provider = SimpleForecastProvider(price_forecaster, load_forecaster)
        result = provider.get_load_forecast("dayah_open", horizon=96, quantiles=True)

        assert result.name == "Q_dayah_open_pre"
        assert result.unit == "MWh/刻"
        assert len(result.point) == 96
        assert result.lower is not None
        assert result.upper is not None

    def test_forecast_contract_assertions(self):
        """ForecastResult should satisfy contract assertions (§14.1)."""
        price_forecaster = SimplePriceForecaster()
        load_forecaster = LoadForecaster()
        provider = SimpleForecastProvider(price_forecaster, load_forecaster)

        result = provider.get_price_forecast("real", horizon=96, quantiles=True)

        # Length = horizon
        assert len(result.point) == 96

        # lower ≤ point ≤ upper
        assert all(result.lower <= result.point)
        assert all(result.point <= result.upper)

        # issue_time present
        assert result.issue_time is not None

        # No NaN
        assert not result.point.isna().any()
