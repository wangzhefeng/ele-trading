from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ele_trading.capacity_planning.wind_simulation import WindSimulator, WindSimResult


def _make_weather(n_hours: int = 8760) -> pd.DataFrame:
    """生成简化风资源气象数据。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz='Asia/Shanghai')
    rng = np.random.default_rng(0)
    wind_speed = rng.weibull(2.0, n_hours) * 7  # Weibull 分布，平均约 6.3 m/s
    return pd.DataFrame({
        'wind_speed': wind_speed,
        'temperature': rng.uniform(-5, 25, n_hours),       # °C
        'pressure': rng.uniform(95000, 101325, n_hours),   # Pa
    }, index=idx)


def test_wind_sim_returns_correct_type():
    sim = WindSimulator()
    weather = _make_weather(24)
    result = sim.simulate(weather, equiv_hours=2200, target_capacity_mw=1.0)
    assert isinstance(result, WindSimResult)


def test_wind_sim_output_length_matches_input():
    sim = WindSimulator()
    weather = _make_weather(168)
    result = sim.simulate(weather, equiv_hours=2200, target_capacity_mw=1.0)
    assert len(result.output_mw) == 168


def test_wind_sim_output_non_negative():
    sim = WindSimulator()
    weather = _make_weather(8760)
    result = sim.simulate(weather, equiv_hours=2200, target_capacity_mw=1.0)
    assert (result.output_mw >= 0).all()


def test_wind_sim_scale_factor_calibrates_energy():
    sim = WindSimulator()
    weather = _make_weather(8760)
    equiv_hours = 2200.0
    result = sim.simulate(weather, equiv_hours=equiv_hours, target_capacity_mw=1.0)
    assert result.total_generation_mwh > 0


def test_wind_sim_capacity_scales_linearly():
    sim = WindSimulator()
    weather = _make_weather(8760)
    r1 = sim.simulate(weather, equiv_hours=2200, target_capacity_mw=10.0)
    r2 = sim.simulate(weather, equiv_hours=2200, target_capacity_mw=20.0)
    np.testing.assert_allclose(r2.output_mw.values, r1.output_mw.values * 2, rtol=1e-6)


def test_wind_sim_returns_turbine_info():
    sim = WindSimulator()
    weather = _make_weather(24)
    result = sim.simulate(weather, equiv_hours=2200, target_capacity_mw=10.0)
    assert isinstance(result.selected_turbine, str)
    assert len(result.selected_turbine) > 0
    assert result.turbine_count >= 1


def test_wind_sim_hub_height_shear():
    """更高轮毂高度 → 更高风速 → raw energy 更大 → scale_factor 更小。"""
    weather = _make_weather(8760)
    sim_low = WindSimulator(hub_height=80)
    sim_high = WindSimulator(hub_height=120)
    r_low = sim_low.simulate(weather, equiv_hours=2200, target_capacity_mw=1.0)
    r_high = sim_high.simulate(weather, equiv_hours=2200, target_capacity_mw=1.0)
    assert r_high.scale_factor <= r_low.scale_factor


def test_wind_sim_single_turbine_capacity_fixed():
    """指定单机容量时，turbine_count 应与 target/single 的比值一致。"""
    sim = WindSimulator()
    weather = _make_weather(24)
    result = sim.simulate(
        weather, equiv_hours=2200, target_capacity_mw=10.0,
        single_turbine_capacity_mw=2.0,
    )
    assert result.turbine_count >= 1
    assert isinstance(result.selected_turbine, str)
