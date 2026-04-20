from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ele_trading.forecasting.solar_forecast import SolarPowerForecaster
from ele_trading.forecasting.base import ForecastOutput


def _make_solar_history(n_hours: int = 8760) -> pd.Series:
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz='Asia/Shanghai')
    hours = np.arange(n_hours) % 24
    output = np.maximum(0.0, np.sin(np.pi * (hours - 6) / 12) * 0.8)
    rng = np.random.default_rng(42)
    output = np.clip(output + rng.normal(0, 0.02, n_hours), 0.0, 1.0)
    return pd.Series(output, index=idx)


def _make_solar_weather(n_hours: int = 24) -> pd.DataFrame:
    idx = pd.date_range('2024-01-01', periods=n_hours, freq='h', tz='Asia/Shanghai')
    rng = np.random.default_rng(0)
    hours = np.arange(n_hours) % 24
    ghi = np.maximum(0.0, np.sin(np.pi * (hours - 6) / 12) * 800)
    return pd.DataFrame({
        'ghi': ghi,
        'temp_air': 15.0 + rng.normal(0, 2, n_hours),
        'wind_speed': rng.uniform(1, 5, n_hours),
    }, index=idx)


def test_solar_harmonic_fit_returns_self():
    fc = SolarPowerForecaster(mode='harmonic')
    result = fc.fit(_make_solar_history(200))
    assert result is fc


def test_solar_harmonic_predict_returns_forecast_output():
    fc = SolarPowerForecaster(mode='harmonic')
    fc.fit(_make_solar_history(200))
    out = fc.predict(horizon=24)
    assert isinstance(out, ForecastOutput)


def test_solar_harmonic_predict_length_matches_horizon():
    fc = SolarPowerForecaster(mode='harmonic')
    fc.fit(_make_solar_history(200))
    for h in (1, 24, 48):
        out = fc.predict(horizon=h)
        assert len(out.point_forecast) == h
        assert len(out.lower_quantile) == h
        assert len(out.upper_quantile) == h


def test_solar_harmonic_predict_non_negative():
    fc = SolarPowerForecaster(mode='harmonic')
    fc.fit(_make_solar_history(8760))
    out = fc.predict(horizon=168)
    assert all(v >= 0 for v in out.point_forecast)
    assert all(v >= 0 for v in out.lower_quantile)


def test_solar_harmonic_quantile_ordering():
    fc = SolarPowerForecaster(mode='harmonic')
    fc.fit(_make_solar_history(8760))
    out = fc.predict(horizon=48)
    for lo, mid, hi in zip(out.lower_quantile, out.point_forecast, out.upper_quantile):
        assert lo <= mid + 1e-9
        assert mid <= hi + 1e-9


def test_solar_harmonic_not_fitted_raises():
    fc = SolarPowerForecaster(mode='harmonic')
    with pytest.raises(RuntimeError, match='fit'):
        fc.predict(horizon=24)


def test_solar_harmonic_insufficient_history_raises():
    fc = SolarPowerForecaster(mode='harmonic')
    with pytest.raises(ValueError, match='48 hours'):
        fc.fit(_make_solar_history(24))


def test_solar_physics_requires_lat_lon():
    with pytest.raises(ValueError, match='latitude and longitude'):
        SolarPowerForecaster(mode='physics')


def test_solar_physics_forecast_length():
    fc = SolarPowerForecaster(mode='physics', latitude=39.9, longitude=116.4)
    weather = _make_solar_weather(48)
    out = fc.forecast_from_weather(weather, capacity_mw=1.0, equiv_hours=1200)
    assert len(out.point_forecast) == 48


def test_solar_physics_forecast_non_negative():
    fc = SolarPowerForecaster(mode='physics', latitude=39.9, longitude=116.4)
    weather = _make_solar_weather(24)
    out = fc.forecast_from_weather(weather, capacity_mw=1.0, equiv_hours=1200)
    assert all(v >= 0 for v in out.point_forecast)
    assert all(lo <= hi + 1e-9 for lo, hi in zip(out.lower_quantile, out.upper_quantile))


def test_solar_invalid_mode_raises():
    with pytest.raises(ValueError, match='mode'):
        SolarPowerForecaster(mode='invalid')
