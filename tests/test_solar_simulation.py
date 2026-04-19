"""
tests/test_solar_simulation.py
TDD 测试套件：SolarSimulator
"""
import numpy as np
import pandas as pd
import pytest

from ele_trading.capacity_planning import SolarSimulator


# ── 共享 Fixture ──────────────────────────────────────────────────────────────

@pytest.fixture
def weather_df():
    """构造一个完整年（8760 小时）的合成气象数据，含白天辐照和夜间零值。"""
    idx = pd.date_range("2024-01-01", periods=8760, freq="h", tz="Asia/Shanghai")
    rng = np.random.default_rng(42)

    # 小时角（0-23），白天（6-18h）给辐照，其余为 0
    hour = idx.hour
    daytime = (hour >= 6) & (hour <= 18)

    ghi = np.where(daytime, rng.uniform(200, 900, 8760), 0.0)
    temp_air = rng.uniform(5, 35, 8760)
    wind_speed = rng.uniform(0, 5, 8760)

    return pd.DataFrame(
        {"ghi": ghi, "temp_air": temp_air, "wind_speed": wind_speed}, index=idx
    )


@pytest.fixture
def simulator():
    """北京坐标，亚洲/上海时区。"""
    return SolarSimulator(latitude=39.9, longitude=116.4, timezone="Asia/Shanghai")


# ── 测试 1：可实例化 ──────────────────────────────────────────────────────────

def test_instantiation():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone="Asia/Shanghai")
    assert sim.latitude == 39.9
    assert sim.longitude == 116.4
    assert sim.timezone == "Asia/Shanghai"


# ── 测试 2：默认倾角为 latitude * 0.9 ────────────────────────────────────────

def test_default_tilt():
    sim = SolarSimulator(latitude=39.9, longitude=116.4, timezone="Asia/Shanghai")
    assert abs(sim.tilt - 39.9 * 0.9) < 1e-9


# ── 测试 3：自定义倾角 ────────────────────────────────────────────────────────

def test_custom_tilt():
    sim = SolarSimulator(
        latitude=39.9, longitude=116.4, timezone="Asia/Shanghai", tilt=30.0
    )
    assert sim.tilt == 30.0


# ── 测试 4：simulate 返回 (Series, float) ──────────────────────────────────

def test_simulate_return_types(simulator, weather_df):
    output, K = simulator.simulate(weather_df, equiv_hours=1300.0)
    assert isinstance(output, pd.Series)
    assert isinstance(K, float)


# ── 测试 5：输出长度与输入一致 ────────────────────────────────────────────────

def test_output_length(simulator, weather_df):
    output, _ = simulator.simulate(weather_df, equiv_hours=1300.0)
    assert len(output) == len(weather_df)


# ── 测试 6：输出全部非负 ──────────────────────────────────────────────────────

def test_output_non_negative(simulator, weather_df):
    output, _ = simulator.simulate(weather_df, equiv_hours=1300.0)
    assert (output >= 0).all()


# ── 测试 7：等效小时数校准正确 ────────────────────────────────────────────────

def test_equiv_hours_calibration(simulator, weather_df):
    equiv_hours = 1300.0
    output, K = simulator.simulate(weather_df, equiv_hours=equiv_hours)
    # 1 MW 基准容量下，年发电量应等于 equiv_hours MWh
    annual_gen = output.sum() * 1.0  # 1h 间隔，直接求和得 MWh
    assert abs(annual_gen - equiv_hours) < 1.0, (
        f"年发电量 {annual_gen:.1f} MWh 偏离目标 {equiv_hours} MWh"
    )


# ── 测试 8：容量缩放正确 ──────────────────────────────────────────────────────

def test_capacity_scaling(simulator, weather_df):
    out_1mw, K1 = simulator.simulate(weather_df, equiv_hours=1300.0, target_capacity_mw=1.0)
    out_5mw, K5 = simulator.simulate(weather_df, equiv_hours=1300.0, target_capacity_mw=5.0)
    ratio = out_5mw.sum() / out_1mw.sum()
    assert abs(ratio - 5.0) < 1e-6, f"容量缩放比例应为 5，实际为 {ratio}"
    # 校准系数 K 与容量无关
    assert abs(K1 - K5) < 1e-9
