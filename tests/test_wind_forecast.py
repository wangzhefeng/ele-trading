from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ele_trading.forecasting.wind_forecast import WindPowerForecaster
from ele_trading.forecasting.base import ForecastOutput


def _make_wind_history(n_hours: int = 8760) -> pd.Series:
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz='Asia/Shanghai')
    rng = np.random.default_rng(0)
    output = np.clip(rng.weibull(2.0, n_hours) * 0.4, 0.0, 1.0)
    return pd.Series(output, index=idx)


def _make_wind_weather(n_hours: int = 24) -> pd.DataFrame:
    idx = pd.date_range('2024-01-01', periods=n_hours, freq='h', tz='Asia/Shanghai')
    rng = np.random.default_rng(1)
    return pd.DataFrame({
        'wind_speed': rng.weibull(2.0, n_hours) * 7,
        'temperature': rng.uniform(-5, 25, n_hours),
        'pressure': rng.uniform(95000, 101325, n_hours),
    }, index=idx)


def test_wind_statistical_fit_returns_self():
    fc = WindPowerForecaster(mode='statistical')
    result = fc.fit(_make_wind_history(500))
    assert result is fc


def test_wind_statistical_predict_returns_forecast_output():
    fc = WindPowerForecaster(mode='statistical')
    fc.fit(_make_wind_history(500))
    out = fc.predict(horizon=24)
    assert isinstance(out, ForecastOutput)


def test_wind_statistical_predict_length_matches_horizon():
    fc = WindPowerForecaster(mode='statistical')
    fc.fit(_make_wind_history(500))
    for h in (1, 24, 72):
        out = fc.predict(horizon=h)
        assert len(out.point_forecast) == h
        assert len(out.lower_quantile) == h
        assert len(out.upper_quantile) == h


def test_wind_statistical_predict_non_negative():
    fc = WindPowerForecaster(mode='statistical')
    fc.fit(_make_wind_history(8760))
    out = fc.predict(horizon=168)
    assert all(v >= 0 for v in out.point_forecast)
    assert all(v >= 0 for v in out.lower_quantile)


def test_wind_statistical_quantile_ordering():
    fc = WindPowerForecaster(mode='statistical')
    fc.fit(_make_wind_history(8760))
    out = fc.predict(horizon=48)
    for lo, mid, hi in zip(out.lower_quantile, out.point_forecast, out.upper_quantile):
        assert lo <= mid + 1e-9
        assert mid <= hi + 1e-9


def test_wind_statistical_not_fitted_raises():
    fc = WindPowerForecaster(mode='statistical')
    with pytest.raises(RuntimeError, match='fit'):
        fc.predict(horizon=24)


def test_wind_insufficient_history_raises():
    fc = WindPowerForecaster(mode='statistical', ar_lags=24)
    short = _make_wind_history(30)
    with pytest.raises(ValueError):
        fc.fit(short)


def test_wind_physics_forecast_length():
    fc = WindPowerForecaster(mode='physics')
    weather = _make_wind_weather(48)
    out = fc.forecast_from_weather(weather, capacity_mw=10.0, equiv_hours=2200)
    assert len(out.point_forecast) == 48


def test_wind_physics_forecast_non_negative():
    fc = WindPowerForecaster(mode='physics')
    weather = _make_wind_weather(24)
    out = fc.forecast_from_weather(weather, capacity_mw=10.0, equiv_hours=2200)
    assert all(v >= 0 for v in out.point_forecast)
    assert all(lo <= hi + 1e-9 for lo, hi in zip(out.lower_quantile, out.upper_quantile))


def test_wind_invalid_mode_raises():
    with pytest.raises(ValueError, match='mode'):
        WindPowerForecaster(mode='bad')
