from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ele_trading.capacity_planning.solar_simulation import SolarSimulator, SolarSimResult


def _make_weather(n_hours: int = 8760) -> pd.DataFrame:
    """生成北京（39.9°N, 116.4°E）简化全年气象数据。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz='Asia/Shanghai')
    rng = np.random.default_rng(42)
    hours = np.arange(n_hours) % 24
    ghi = np.maximum(0, np.sin(np.pi * (hours - 6) / 12) * 800)
    return pd.DataFrame({
        'ghi': ghi,
        'temp_air': 15 + 10 * np.sin(2 * np.pi * np.arange(n_hours) / 8760),
        'wind_speed': rng.uniform(1, 5, n_hours),
    }, index=idx)


def test_solar_sim_returns_correct_type():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone='Asia/Shanghai')
    weather = _make_weather(24)
    result = sim.simulate(weather, equiv_hours=1200, target_capacity_mw=1.0)
    assert isinstance(result, SolarSimResult)


def test_solar_sim_output_length_matches_input():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone='Asia/Shanghai')
    weather = _make_weather(168)
    result = sim.simulate(weather, equiv_hours=1200, target_capacity_mw=1.0)
    assert len(result.output_mw) == 168


def test_solar_sim_output_non_negative():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone='Asia/Shanghai')
    weather = _make_weather(8760)
    result = sim.simulate(weather, equiv_hours=1200, target_capacity_mw=1.0)
    assert (result.output_mw >= 0).all()


def test_solar_sim_output_bounded_by_capacity():
    capacity_mw = 5.0
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone='Asia/Shanghai')
    weather = _make_weather(8760)
    result = sim.simulate(weather, equiv_hours=1200, target_capacity_mw=capacity_mw)
    assert result.output_mw.max() <= capacity_mw * 1.1


def test_solar_sim_scale_factor_calibrates_energy():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone='Asia/Shanghai')
    weather = _make_weather(8760)
    equiv_hours = 1200.0
    result = sim.simulate(weather, equiv_hours=equiv_hours, target_capacity_mw=1.0)
    assert abs(result.total_generation_mwh - equiv_hours) / equiv_hours < 0.01


def test_solar_sim_capacity_scales_linearly():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone='Asia/Shanghai')
    weather = _make_weather(8760)
    r1 = sim.simulate(weather, equiv_hours=1200, target_capacity_mw=1.0)
    r2 = sim.simulate(weather, equiv_hours=1200, target_capacity_mw=10.0)
    np.testing.assert_allclose(r2.output_mw.values, r1.output_mw.values * 10, rtol=1e-6)


def test_solar_sim_default_tilt_uses_latitude():
    sim = SolarSimulator(latitude=30.0, longitude=120.0)
    assert abs(sim.tilt - 30.0 * 0.9) < 1e-9


def test_solar_sim_explicit_tilt():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, tilt=25.0)
    assert sim.tilt == 25.0
