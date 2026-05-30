"""容量规划优化测试。"""

import numpy as np
import pandas as pd
import pytest
from ele_trading.capacity_planning.capacity_optimizer import (
    CapacityOptimizer,
    simulate_operation,
    CapacityPlanResult,
    simple_energy_sanity_check,
    curve_based_energy_check,
)
from ele_trading.utils.time_index import infer_dt_hours, monthly_kwh


def _make_data(n=48):
    """构造 48 小时测试数据。"""
    rng = np.random.default_rng(42)
    idx = pd.date_range('2024-07-01', periods=n, freq='h')
    load = pd.Series(np.clip(rng.normal(50, 15, n), 10, 100), index=idx)
    # 单位出力（每 MW 装机）在 [0, 1] 之间
    wind_u = pd.Series(np.clip(np.abs(rng.normal(0.3, 0.2, n)), 0, 1), index=idx)
    pv_u = pd.Series(
        np.where((idx.hour >= 8) & (idx.hour <= 17), np.abs(rng.normal(0.5, 0.2, n)), 0.0),
        index=idx,
    )
    return load, wind_u, pv_u


def test_simulate_operation_returns_fields():
    """simulate_operation 返回应有六个字段。"""
    load, wind_u, pv_u = _make_data()
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    result = simulate_operation(load, wind_u, pv_u, wind_mw=30, pv_mw=20, ess_mwh=40, bess_params=sp)
    for field in ('green_ratio', 'self_use_ratio', 'curtailment_ratio',
                  'total_green_gen_mwh', 'total_grid_buy_mwh', 'total_curtailment_mwh'):
        assert field in result


def test_green_ratio_in_range():
    """green_ratio 应在 [0, 1]。"""
    load, wind_u, pv_u = _make_data()
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    result = simulate_operation(load, wind_u, pv_u, wind_mw=50, pv_mw=50, ess_mwh=100, bess_params=sp)
    assert 0.0 <= result['green_ratio'] <= 1.0
    assert 0.0 <= result['self_use_ratio'] <= 1.0


def test_capacity_optimizer_find_plan():
    """给定宽松约束，应找到可行容量方案。"""
    load, wind_u, pv_u = _make_data(72)
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    cost = {'wind_yuan_per_kw': 5000, 'pv_yuan_per_kw': 3500, 'ess_yuan_per_kwh': 1500}
    opt = CapacityOptimizer(bess_params=sp, cost_params=cost)
    plan = opt.optimize(load, wind_u, pv_u, green_ratio_min=0.3, self_use_ratio_min=0.3)
    assert isinstance(plan, CapacityPlanResult)
    assert plan.green_ratio >= 0.3 - 1e-9


def test_capacity_optimizer_infeasible():
    """极大约束下应抛出 ValueError。"""
    load, wind_u, pv_u = _make_data(24)
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    cost = {'wind_yuan_per_kw': 5000, 'pv_yuan_per_kw': 3500, 'ess_yuan_per_kwh': 1500}
    # 使用严格搜索参数限制搜索空间
    search = {
        'coarse_step_mw': 50,
        'coarse_step_mwh': 50,
        'max_wind_mw': 5,
        'max_pv_mw': 5,
        'max_ess_mwh': 5,
    }
    opt = CapacityOptimizer(bess_params=sp, cost_params=cost, search_params=search)
    with pytest.raises(ValueError, match='feasible'):
        opt.optimize(load, wind_u, pv_u, green_ratio_min=0.99, self_use_ratio_min=0.99)


def test_pv_only_mode():
    """fixed_wind_mw=0 时应退化为 PV-only 搜索。"""
    load, _, pv_u = _make_data(72)
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    cost = {'wind_yuan_per_kw': 5000, 'pv_yuan_per_kw': 3500, 'ess_yuan_per_kwh': 1500}
    search = {'coarse_step_mw': 10, 'max_pv_mw': 200, 'max_ess_mwh': 0, 'coarse_step_mwh': 1}
    opt = CapacityOptimizer(bess_params=sp, cost_params=cost, search_params=search)
    plan = opt.optimize(load, pd.Series(0.0, index=load.index), pv_u,
                        green_ratio_min=0.1, self_use_ratio_min=0.3,
                        fixed_wind_mw=0.0)
    assert plan.wind_mw == 0.0
    assert plan.pv_mw > 0


def test_eta_roundtrip_dispatch():
    """eta_roundtrip 参数应通过 sqrt 分配给充放电效率。"""
    load, wind_u, pv_u = _make_data()
    sp_rt = {'eta_roundtrip': 0.90, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    sp_sep = {'eta_charge': 0.90**0.5, 'eta_discharge': 0.90**0.5, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    r1 = simulate_operation(load, wind_u, pv_u, 30, 20, 40, sp_rt)
    r2 = simulate_operation(load, wind_u, pv_u, 30, 20, 40, sp_sep)
    assert abs(r1['green_ratio'] - r2['green_ratio']) < 1e-9
    assert abs(r1['self_use_ratio'] - r2['self_use_ratio']) < 1e-9


def test_pv_monthly_kwh_in_result():
    """优化结果应包含 pv_monthly_kwh。"""
    load, wind_u, pv_u = _make_data(72)
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    cost = {'wind_yuan_per_kw': 5000, 'pv_yuan_per_kw': 3500, 'ess_yuan_per_kwh': 1500}
    opt = CapacityOptimizer(bess_params=sp, cost_params=cost)
    plan = opt.optimize(load, wind_u, pv_u, green_ratio_min=0.3, self_use_ratio_min=0.3)
    if plan.pv_mw > 0:
        assert plan.pv_monthly_kwh is not None
        assert isinstance(plan.pv_monthly_kwh, pd.Series)
        assert len(plan.pv_monthly_kwh) > 0


def test_infer_dt_hours():
    """infer_dt_hours 应正确推断小时级步长。"""
    idx = pd.date_range('2024-01-01', periods=48, freq='h')
    assert abs(infer_dt_hours(idx) - 1.0) < 1e-9

    idx_15min = pd.date_range('2024-01-01', periods=96, freq='15min')
    assert abs(infer_dt_hours(idx_15min) - 0.25) < 1e-9


def test_monthly_kwh():
    """monthly_kwh 应按月汇总电量。"""
    idx = pd.date_range('2024-01-01', periods=744, freq='h')  # 31 days
    kw = np.ones(744) * 100.0
    result = monthly_kwh(idx, kw, 1.0)
    assert len(result) == 1
    assert abs(result.iloc[0] - 74400.0) < 1e-6


def test_simple_energy_sanity_check():
    """simple_energy_sanity_check 应返回合理的 PV 容量估算。"""
    load, _, _ = _make_data(8760)  # 一年
    result = simple_energy_sanity_check(load, green_ratio_min=0.3, self_use_min=0.6)
    assert 'load_gwh_year' in result
    assert 'gen_required_gwh' in result
    assert 'pv_required_table' in result
    table = result['pv_required_table']
    assert len(table) == 4  # default 4 yield levels
    assert all(table['pv_required_MWp'] > 0)
    # 年利用小时越高，所需 PV 越小
    assert table['pv_required_MWp'].is_monotonic_decreasing


def test_curve_based_energy_check():
    """curve_based_energy_check 应基于实际曲线估算 PV 容量。"""
    load, _, pv_u = _make_data(8760)
    result = curve_based_energy_check(load, pv_u, green_ratio_min=0.3, self_use_min=0.6)
    assert 'load_gwh_year' in result
    assert 'yield_curve_kWh_per_kWp' in result
    assert 'pv_required_MWp' in result
    assert result['pv_required_MWp'] > 0
    assert result['yield_curve_kWh_per_kWp'] > 0
