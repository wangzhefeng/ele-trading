"""Unit tests for DR allocator and forecast provider."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.forecasting.load_forecast import LoadForecaster
from ele_trading.forecasting.price_forecast import SimplePriceForecaster
from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.forecasting.provider import SimpleForecastProvider, assert_no_future_info
from ele_trading.trading.contracts import MarketConfig
from ele_trading.demand_response.allocator import (
    estimate_arbitrage_opportunity_cost,
    evaluate_dr_participation,
)


@pytest.fixture
def config():
    return MarketConfig()


def _price_history() -> pd.Series:
    index = pd.date_range(
        end=pd.Timestamp("2026-06-30 23:45", tz="Asia/Shanghai"),
        periods=96,
        freq="15min",
    )
    return pd.Series(300.0, index=index)


class TestDRAllocator:
    def test_participate_when_compensation_high(self, config):
        """Should participate when DR compensation > opportunity cost."""
        adjustable = np.full(96, 5.0)  # 5 MW available
        config.dr_compensation_per_mwh = 2000.0
        config.dr_window_start = 40
        config.dr_window_end = 60
        decision = evaluate_dr_participation(
            adjustable,
            config,
            p_net_plan=np.zeros(96),
            realtime_price_forecast=np.full(96, 300.0),
        )
        assert decision.participate
        assert decision.response_qty > 0
        assert decision.expected_compensation > decision.arbitrage_opportunity_cost

    def test_reject_when_compensation_low(self, config):
        """Should reject when DR compensation < opportunity cost."""
        adjustable = np.full(96, 5.0)
        config.dr_compensation_per_mwh = 10.0
        config.dr_window_start = 40
        config.dr_window_end = 60
        p_net_plan = np.zeros(96)
        p_net_plan[40:60] = 3.0
        decision = evaluate_dr_participation(
            adjustable,
            config,
            p_net_plan=p_net_plan,
            realtime_price_forecast=np.full(96, 300.0),
        )
        assert not decision.participate
        assert decision.reject_reason is not None

    def test_opportunity_cost_from_plan(self, config):
        """机会成本由日前计划实算：高价窗口放电计划的套利收益应被计入（§9）。"""
        horizon = 96
        p_real_pre = np.full(horizon, 300.0)
        p_real_pre[40:60] = 800.0  # 响应窗口为高价时段
        p_b_plan = np.zeros(horizon)
        p_b_plan[40:60] = 3.0  # 计划窗口内 3MW 放电套利

        cost = estimate_arbitrage_opportunity_cost(
            p_b_plan,
            p_real_pre,
            (40, 60),
            dt=config.dt,
        )
        expected = 3.0 * 800.0 * 0.25 * 20
        assert cost == pytest.approx(expected)

        # 用实算机会成本做决策：补偿低于实算成本时拒绝
        adjustable = np.full(horizon, 3.0)
        config.dr_compensation_per_mwh = 500.0
        config.dr_window_start = 40
        config.dr_window_end = 60
        decision = evaluate_dr_participation(
            adjustable,
            config,
            p_net_plan=p_b_plan,
            realtime_price_forecast=p_real_pre,
        )
        # 补偿 500*15=7500 < 机会成本 12000 → 拒绝
        assert not decision.participate


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
        provider = SimpleForecastProvider(
            price_forecaster,
            load_forecaster,
            default_history_prices=_price_history(),
        )
        request = ForecastRequest(
            target="price",
            scope_type="market",
            scope_id="dayah",
            horizon=96,
            frequency="15min",
            issue_time=pd.Timestamp(
                "2026-07-01 00:00",
                tz="Asia/Shanghai",
            ),
            quantiles=(0.05, 0.95),
        )

        result = provider.get_price_forecast(request)
        assert result.request is request
        assert result.unit == "CNY/MWh"
        assert len(result.point) == 96
        assert all(result.quantiles[0.05] <= result.point)
        assert all(result.point <= result.quantiles[0.95])

    def test_load_forecast(self):
        """Provider should return valid load forecast."""
        price_forecaster = SimplePriceForecaster()
        load_forecaster = LoadForecaster()

        # Fit load forecaster
        dates = pd.date_range(
            "2026-01-01",
            periods=30 * 96,
            freq="15min",
            tz="Asia/Shanghai",
        )
        hour = dates.hour + dates.minute / 60
        load = 100 + 20 * np.sin(2 * np.pi * hour / 24)
        load_series = pd.Series(load, index=dates)
        load_forecaster.fit(load_series)

        provider = SimpleForecastProvider(price_forecaster, load_forecaster)
        request = ForecastRequest(
            target="load",
            scope_type="market",
            scope_id="dayah_open",
            horizon=96,
            frequency="15min",
            issue_time=pd.Timestamp(
                "2026-07-01 00:00",
                tz="Asia/Shanghai",
            ),
            quantiles=(0.1, 0.9),
        )
        result = provider.get_load_forecast(request)

        assert result.request is request
        assert result.unit == "MWh/period"
        assert len(result.point) == 96
        assert tuple(result.quantiles) == (0.1, 0.9)

    def test_forecast_contract_assertions(self):
        """ForecastResult should satisfy contract assertions (§14.1)."""
        price_forecaster = SimplePriceForecaster()
        load_forecaster = LoadForecaster()
        provider = SimpleForecastProvider(
            price_forecaster,
            load_forecaster,
            default_history_prices=_price_history(),
        )
        request = ForecastRequest(
            target="price",
            scope_type="market",
            scope_id="real",
            horizon=96,
            frequency="15min",
            issue_time=pd.Timestamp(
                "2026-07-01 00:00",
                tz="Asia/Shanghai",
            ),
            quantiles=(0.05, 0.95),
        )

        result = provider.get_price_forecast(request)

        # Length = horizon
        assert len(result.point) == 96

        # lower ≤ point ≤ upper
        assert all(result.quantiles[0.05] <= result.point)
        assert all(result.point <= result.quantiles[0.95])

        # issue_time present
        assert result.request.issue_time is not None

        # No NaN
        assert not result.point.isna().any()


class TestForecastIssueTime:
    def test_issue_time_injectable(self):
        """显式 issue_time 应透传到 ForecastResult（§4.1 幂等可复现）。"""
        provider = SimpleForecastProvider(
            SimplePriceForecaster(),
            LoadForecaster(),
            default_history_prices=_price_history(),
        )
        issue = pd.Timestamp(
            "2026-07-01 00:00",
            tz="Asia/Shanghai",
        )
        request = ForecastRequest(
            target="price",
            scope_type="market",
            scope_id="dayah",
            horizon=96,
            frequency="15min",
            issue_time=issue,
        )
        result = provider.get_price_forecast(request)
        assert result.request.issue_time == issue

    def test_no_future_info_rejected(self):
        """issue_time 晚于决策时刻时应报错（§4.1 无前瞻约束）。"""
        provider = SimpleForecastProvider(
            SimplePriceForecaster(),
            LoadForecaster(),
            default_history_prices=_price_history(),
        )
        result = provider.get_price_forecast(
            ForecastRequest(
                target="price",
                scope_type="market",
                scope_id="dayah",
                horizon=96,
                frequency="15min",
                issue_time=pd.Timestamp(
                    "2026-07-01 12:00",
                    tz="Asia/Shanghai",
                ),
            )
        )
        with pytest.raises(ValueError, match="future"):
            assert_no_future_info(
                result,
                pd.Timestamp(
                    "2026-07-01 00:00",
                    tz="Asia/Shanghai",
                ),
            )
        # 不晚于决策时刻时放行
        assert_no_future_info(
            result,
            pd.Timestamp(
                "2026-07-01 13:00",
                tz="Asia/Shanghai",
            ),
        )
