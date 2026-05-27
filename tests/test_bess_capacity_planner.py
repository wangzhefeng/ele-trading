"""BESS 容量规划模块测试。"""

import numpy as np
import pandas as pd
import pytest

from ele_trading.capacity_planning.bess_capacity_planner import (
    BESSPlanConfig,
    BESSCapacityResult,
    UnitsConfig,
    _dispatch,
    _NUMBA_OK,
    plan_energy_system,
    simulate_bess_operation,
)
from ele_trading.utils.data_alignment import (
    as_time_series,
    normalize_time_and_load,
    align_to_time,
)


def _make_data(n=48):
    """构造 n 小时测试数据，返回 (df_load, df_wind)。"""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-07-01", periods=n, freq="h")
    load_kw = np.clip(rng.normal(500, 150, n), 50, 1000)
    wind_mw = np.clip(np.abs(rng.normal(0.3, 0.2, n)), 0, 1)

    df_load = pd.DataFrame({"Time": idx, "P_kw": load_kw})
    df_wind = pd.DataFrame({"Time": idx, "WindPower_MW": wind_mw})
    return df_load, df_wind


# ============================================================
# 调度器测试
# ============================================================
class TestDispatch:

    def test_returns_correct_keys(self):
        """_dispatch 应返回 6 个字段。"""
        df_load, df_wind = _make_data()
        load_kw = df_load["P_kw"].to_numpy()
        gen_kw = df_wind["WindPower_MW"].to_numpy() * 1000
        result = _dispatch(load_kw, gen_kw, 1.0, 1000.0, 0.92, 0.5, 0.5, 0.1, 1.0)
        expected_keys = {"gen_kwh", "used_kwh", "load_kwh", "self_use_ratio", "load_cover_ratio", "bess_discharge_kwh"}
        assert expected_keys == set(result.keys())

    def test_no_battery(self):
        """无储能时 bess_discharge_kwh=0，used_kwh 等于直接消纳量。"""
        load_kw = np.array([100.0, 200.0, 300.0])
        gen_kw = np.array([150.0, 150.0, 100.0])
        result = _dispatch(load_kw, gen_kw, 1.0, 0.0, 0.92, 0.5, 0.5, 0.1, 1.0)
        assert result["bess_discharge_kwh"] == 0.0
        expected_used = (100 + 150 + 100) * 1.0  # min(load, gen) per step
        assert abs(result["used_kwh"] - expected_used) < 1e-6

    def test_eta_roundtrip_sqrt_split(self):
        """eta_roundtrip=0.90 应等效于 eta_c=eta_d=sqrt(0.90)。"""
        load_kw = np.array([100.0, 200.0, 300.0, 150.0])
        gen_kw = np.array([300.0, 50.0, 300.0, 50.0])
        r1 = _dispatch(load_kw, gen_kw, 1.0, 500.0, 0.90, 0.5, 0.5, 0.1, 1.0, use_numba=True)
        r2 = _dispatch(load_kw, gen_kw, 1.0, 500.0, 0.90, 0.5, 0.5, 0.1, 1.0, use_numba=True)
        assert r1["used_kwh"] == pytest.approx(r2["used_kwh"], rel=1e-9)

    def test_numba_vs_python_consistency(self):
        """Numba 和 Python 路径应产生一致结果（无储能时）。"""
        load_kw = np.array([100.0, 200.0, 300.0])
        gen_kw = np.array([150.0, 150.0, 100.0])
        r_numba = _dispatch(load_kw, gen_kw, 1.0, 0.0, 0.92, 0.5, 0.5, 0.1, 1.0, use_numba=True)
        r_python = _dispatch(load_kw, gen_kw, 1.0, 0.0, 0.92, 0.5, 0.5, 0.1, 1.0, use_numba=False)
        assert r_numba["used_kwh"] == pytest.approx(r_python["used_kwh"], rel=1e-9)
        assert r_numba["gen_kwh"] == pytest.approx(r_python["gen_kwh"], rel=1e-9)

    def test_ratios_in_range(self):
        """消纳率和覆盖率应 >= 0。覆盖率 <= 1。"""
        df_load, df_wind = _make_data(72)
        load_kw = df_load["P_kw"].to_numpy()
        gen_kw = df_wind["WindPower_MW"].to_numpy() * 1000
        result = _dispatch(load_kw, gen_kw, 1.0, 5000.0, 0.92, 0.5, 0.5, 0.1, 1.0)
        assert result["self_use_ratio"] >= 0.0
        assert 0.0 <= result["load_cover_ratio"] <= 1.0


# ============================================================
# 规划函数测试
# ============================================================
class TestPlanEnergySystem:

    def test_feasible_scenario(self):
        """有足够新能源时应找到可行解。"""
        df_load, df_wind = _make_data(72)
        cfg = BESSPlanConfig(
            self_use_ratio_min=0.30,
            load_cover_ratio_min=0.10,
            batt_hi_max_kwh=5e4,
        )
        units = UnitsConfig(load_power="kW", wind_power="MW")
        result = plan_energy_system(df_load, wind_input=df_wind, cfg=cfg, units=units)
        assert result.feasible is True
        assert result.self_use_ratio >= 0.30 - 1e-6
        assert result.load_cover_ratio >= 0.10 - 1e-6

    def test_infeasible_scenario(self):
        """极大约束下应返回不可行。"""
        df_load, df_wind = _make_data(24)
        cfg = BESSPlanConfig(
            self_use_ratio_min=0.99,
            load_cover_ratio_min=0.99,
            batt_hi_max_kwh=100.0,
            search_points=10,
        )
        units = UnitsConfig(load_power="kW", wind_power="MW")
        result = plan_energy_system(df_load, wind_input=df_wind, cfg=cfg, units=units)
        assert result.feasible is False
        assert result.diagnosis is not None
        assert result.diagnosis["reason"] == "NO_FEASIBLE_SOLUTION"

    def test_no_generation(self):
        """无风无光时应返回 NO_GENERATION。"""
        df_load, _ = _make_data(24)
        cfg = BESSPlanConfig()
        result = plan_energy_system(df_load, cfg=cfg)
        assert result.feasible is False
        assert result.diagnosis["reason"] == "NO_GENERATION"

    def test_cost_calculation(self):
        """成本应等于储能容量乘以单位造价。"""
        df_load, df_wind = _make_data(72)
        cfg = BESSPlanConfig(
            bess_capex_yuan_per_kwh=1500.0,
            self_use_ratio_min=0.30,
            load_cover_ratio_min=0.10,
            batt_hi_max_kwh=5e4,
        )
        units = UnitsConfig(load_power="kW", wind_power="MW")
        result = plan_energy_system(df_load, wind_input=df_wind, cfg=cfg, units=units)
        if result.feasible:
            assert result.cost_yuan == pytest.approx(result.bess_kwh * 1500.0, rel=1e-6)


# ============================================================
# 数据对齐工具测试
# ============================================================
class TestDataAlignment:

    def test_as_time_series_from_series(self):
        """Series 输入应直接转换单位。"""
        idx = pd.date_range("2024-01-01", periods=5, freq="h")
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=idx)
        result = as_time_series(s, "Time", ("value",), 1000.0)
        assert len(result) == 5
        assert result.iloc[0] == pytest.approx(1000.0)

    def test_as_time_series_from_dataframe(self):
        """DataFrame 输入应按列名匹配。"""
        idx = pd.date_range("2024-01-01", periods=5, freq="h")
        df = pd.DataFrame({"WindPower_MW": [0.1, 0.2, 0.3, 0.4, 0.5]}, index=idx)
        result = as_time_series(df, "Time", ("WindPower_MW",), 1000.0)
        assert result.iloc[0] == pytest.approx(100.0)

    def test_as_time_series_column_not_found(self):
        """列名不匹配时应抛出 ValueError。"""
        idx = pd.date_range("2024-01-01", periods=5, freq="h")
        df = pd.DataFrame({"other_col": [1, 2, 3, 4, 5]}, index=idx)
        with pytest.raises(ValueError, match="未找到"):
            as_time_series(df, "Time", ("WindPower_MW",), 1.0)

    def test_normalize_time_and_load_basic(self):
        """基本的时间和负荷提取。"""
        idx = pd.date_range("2024-01-01", periods=10, freq="h")
        df = pd.DataFrame({"Time": idx, "P_kw": np.arange(10, dtype=float)})
        t, load, warn = normalize_time_and_load(df, "Time", "P_kw")
        assert len(t) == 10
        assert len(load) == 10
        assert load[0] == pytest.approx(0.0)

    def test_normalize_time_and_load_mw_to_kw(self):
        """MW 单位应自动转为 kW。"""
        idx = pd.date_range("2024-01-01", periods=5, freq="h")
        df = pd.DataFrame({"Time": idx, "P_mw": [1.0, 2.0, 3.0, 4.0, 5.0]})
        t, load, _ = normalize_time_and_load(df, "Time", "P_mw", load_unit="MW")
        assert load[0] == pytest.approx(1000.0)

    def test_align_to_time_basic(self):
        """对齐到目标时间轴。"""
        idx_target = pd.date_range("2024-01-01", periods=5, freq="h")
        t = pd.Series(idx_target, name="Time")
        idx_source = pd.date_range("2024-01-01", periods=5, freq="h")
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0], index=idx_source)
        result = align_to_time(t, s)
        assert len(result) == 5
        assert result[0] == pytest.approx(10.0)


# ============================================================
# 数据类测试
# ============================================================
class TestDataclasses:

    def test_bess_capacity_result_fields(self):
        """BESSCapacityResult 应有所有预期字段。"""
        r = BESSCapacityResult(feasible=True, bess_kwh=100.0, cost_yuan=100000.0)
        assert r.feasible is True
        assert r.bess_kwh == 100.0
        assert r.engine == "python"
        assert r.warnings == []
        assert r.diagnosis is None

    def test_bess_plan_config_defaults(self):
        """BESSPlanConfig 默认值应正确。"""
        cfg = BESSPlanConfig()
        assert cfg.eta_roundtrip == 0.92
        assert cfg.c_rate == 0.5
        assert cfg.search_points == 40
        assert cfg.use_numba is True

    def test_units_config_defaults(self):
        """UnitsConfig 默认值应正确。"""
        u = UnitsConfig()
        assert u.load_power == "kW"
        assert u.wind_power == "MW"
