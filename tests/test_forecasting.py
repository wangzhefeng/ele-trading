"""预测模块测试。"""

import numpy as np
import pandas as pd
import pytest
from ele_trading.forecasting.price_forecast import SimplePriceForecaster, ARIMAForecaster
from ele_trading.forecasting.pv_forecast import PVPowerForecaster
from ele_trading.forecasting.wind_forecast import WindPowerForecaster


class TestSimplePriceForecaster:
    def test_basic_forecast(self):
        fc = SimplePriceForecaster()
        history = [300.0, 320.0, 280.0, 310.0, 350.0]
        result = fc.predict(history, horizon=6)
        assert result.horizon == 6
        assert len(result.point_forecast) == 6
        assert len(result.lower_quantile) == 6
        assert len(result.upper_quantile) == 6

    def test_empty_history_raises(self):
        fc = SimplePriceForecaster()
        with pytest.raises(ValueError, match='不能为空'):
            fc.predict([], horizon=3)

    def test_upper_ge_point(self):
        fc = SimplePriceForecaster()
        history = [300.0, 350.0, 280.0]
        result = fc.predict(history, horizon=4)
        for p, u in zip(result.point_forecast, result.upper_quantile):
            assert u >= p - 1e-12

    def test_lower_non_negative(self):
        fc = SimplePriceForecaster()
        history = [300.0, 320.0]
        result = fc.predict(history, horizon=4)
        for lo in result.lower_quantile:
            assert lo >= 0.0


class TestPVPowerForecaster:
    def test_harmonic_fit_predict(self):
        """傅里叶谐波模式 fit + predict 基本流程。"""
        fc = PVPowerForecaster(mode='harmonic')
        idx = pd.date_range('2024-06-01', periods=72, freq='h')
        # 模拟日照曲线：白天有出力，夜间为 0
        hour = idx.hour
        output = pd.Series(
            np.where((hour >= 8) & (hour <= 17), 50.0 * np.sin(np.pi * (hour - 8) / 9), 0.0),
            index=idx,
        )
        fc.fit(output)
        result = fc.predict(horizon=24)
        assert result.horizon == 24
        assert len(result.point_forecast) == 24
        # 夜间预测应为 0
        # (start 默认在最后训练点+1h)

    def test_fit_requires_datetime_index(self):
        fc = PVPowerForecaster(mode='harmonic')
        with pytest.raises(TypeError):
            fc.fit(pd.Series([1.0, 2.0, 3.0]))

    def test_predict_before_fit_raises(self):
        fc = PVPowerForecaster(mode='harmonic')
        with pytest.raises(RuntimeError, match='Call fit'):
            fc.predict(horizon=5)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode must be"):
            PVPowerForecaster(mode='invalid')


class TestWindPowerForecaster:
    def test_statistical_fit_predict(self):
        """统计模式 fit + predict 基本流程。"""
        fc = WindPowerForecaster(mode='statistical', ar_lags=12)
        idx = pd.date_range('2024-01-01', periods=100, freq='h')
        rng = np.random.default_rng(42)
        output = pd.Series(
            np.clip(rng.normal(30, 15, 100), 0, 100),
            index=idx,
        )
        fc.fit(output)
        result = fc.predict(horizon=24)
        assert result.horizon == 24
        assert len(result.point_forecast) == 24

    def test_fit_needs_min_history(self):
        fc = WindPowerForecaster(mode='statistical', ar_lags=12)
        idx = pd.date_range('2024-01-01', periods=20, freq='h')
        output = pd.Series(np.ones(20), index=idx)
        with pytest.raises(ValueError, match='Need at least'):
            fc.fit(output)

    def test_predict_before_fit_raises(self):
        fc = WindPowerForecaster(mode='statistical')
        with pytest.raises(RuntimeError, match='Call fit'):
            fc.predict(horizon=5)

    def test_point_within_capacity(self):
        """预测值应在 [0, capacity] 区间内。"""
        fc = WindPowerForecaster(mode='statistical', ar_lags=12)
        idx = pd.date_range('2024-01-01', periods=80, freq='h')
        rng = np.random.default_rng(7)
        output = pd.Series(
            np.clip(rng.normal(40, 10, 80), 0, 80),
            index=idx,
        )
        fc.fit(output)
        result = fc.predict(horizon=12)
        cap = fc._capacity_mw
        for p in result.point_forecast:
            assert 0.0 <= p <= cap + 1e-10


class TestARIMAForecaster:
    def test_basic_fit_predict(self):
        """ARIMA fit + predict 基本流程。"""
        fc = ARIMAForecaster(order=(2, 0, 1))
        history = [300.0, 320.0, 280.0, 310.0, 350.0, 340.0, 360.0, 330.0, 310.0, 290.0]
        fc.fit(history)
        result = fc.predict(horizon=6)
        assert result.horizon == 6
        assert len(result.point_forecast) == 6
        assert len(result.lower_quantile) == 6
        assert len(result.upper_quantile) == 6

    def test_empty_history_raises(self):
        fc = ARIMAForecaster()
        with pytest.raises(ValueError, match='不能为空'):
            fc.fit([])

    def test_predict_before_fit_raises(self):
        fc = ARIMAForecaster()
        with pytest.raises(RuntimeError, match='Call fit'):
            fc.predict(horizon=5)

    def test_upper_ge_point_ge_lower(self):
        """上分位 >= 点预测 >= 下分位。"""
        fc = ARIMAForecaster(order=(1, 0, 0))
        history = [300 + 20 * np.sin(i * 0.3) for i in range(30)]
        fc.fit(history)
        result = fc.predict(horizon=6)
        for lo, p, hi in zip(result.lower_quantile, result.point_forecast, result.upper_quantile):
            assert hi >= p - 1e-10
            assert p >= lo - 1e-10

    def test_point_forecast_non_negative(self):
        """点预测应非负（已做 max(0, ...) 截断）。"""
        fc = ARIMAForecaster(order=(1, 0, 0))
        history = [300.0, 320.0, 280.0, 310.0, 350.0, 340.0, 360.0, 330.0, 310.0, 290.0]
        fc.fit(history)
        result = fc.predict(horizon=6)
        for p in result.point_forecast:
            assert p >= 0.0

    def test_output_matches_simple_interface(self):
        """ARIMAForecaster 与 SimplePriceForecaster 返回相同 ForecastOutput 结构。"""
        history = [300.0, 320.0, 280.0, 310.0, 350.0]
        simple = SimplePriceForecaster().predict(history, horizon=4)
        arima = ARIMAForecaster(order=(1, 0, 0))
        arima.fit(history)
        arima_result = arima.predict(horizon=4)
        assert simple.horizon == arima_result.horizon
        assert len(simple.point_forecast) == len(arima_result.point_forecast)
        assert len(simple.lower_quantile) == len(arima_result.lower_quantile)
        assert len(simple.upper_quantile) == len(arima_result.upper_quantile)
