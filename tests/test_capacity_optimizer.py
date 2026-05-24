"""容量规划优化测试。"""

import numpy as np
import pandas as pd
import pytest
from ele_trading.capacity_planning.capacity_optimizer import (
    CapacityOptimizer,
    simulate_operation,
    CapacityPlanResult,
)


def _make_data(n=48):
    """构造 48 小时测试数据。"""
    rng = np.random.default_rng(42)
    idx = pd.date_range('2024-07-01', periods=n, freq='h')
    load = pd.Series(np.clip(rng.normal(50, 15, n), 10, 100), index=idx)
    # 单位出力（每 MW 装机）在 [0, 1] 之间
    wind_u = pd.Series(np.clip(np.abs(rng.normal(0.3, 0.2, n)), 0, 1), index=idx)
    solar_u = pd.Series(
        np.where((idx.hour >= 8) & (idx.hour <= 17), np.abs(rng.normal(0.5, 0.2, n)), 0.0),
        index=idx,
    )
    return load, wind_u, solar_u


def test_simulate_operation_returns_fields():
    """simulate_operation 返回应有六个字段。"""
    load, wind_u, solar_u = _make_data()
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    result = simulate_operation(load, wind_u, solar_u, wind_mw=30, pv_mw=20, ess_mwh=40, storage_params=sp)
    for field in ('green_ratio', 'self_use_ratio', 'curtailment_ratio',
                  'total_green_gen_mwh', 'total_grid_buy_mwh', 'total_curtailment_mwh'):
        assert field in result


def test_green_ratio_in_range():
    """green_ratio 应在 [0, 1]。"""
    load, wind_u, solar_u = _make_data()
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    result = simulate_operation(load, wind_u, solar_u, wind_mw=50, pv_mw=50, ess_mwh=100, storage_params=sp)
    assert 0.0 <= result['green_ratio'] <= 1.0
    assert 0.0 <= result['self_use_ratio'] <= 1.0


def test_capacity_optimizer_find_plan():
    """给定宽松约束，应找到可行容量方案。"""
    load, wind_u, solar_u = _make_data(72)
    sp = {'eta_charge': 0.95, 'eta_discharge': 0.95, 'dod': 0.9, 'soc_min': 0.1, 'c_rate': 0.5}
    cost = {'wind_yuan_per_kw': 5000, 'pv_yuan_per_kw': 3500, 'ess_yuan_per_kwh': 1500}
    opt = CapacityOptimizer(storage_params=sp, cost_params=cost)
    plan = opt.optimize(load, wind_u, solar_u, green_ratio_min=0.3, self_use_ratio_min=0.3)
    assert isinstance(plan, CapacityPlanResult)
    assert plan.green_ratio >= 0.3 - 1e-9


def test_capacity_optimizer_infeasible():
    """极大约束下应抛出 ValueError。"""
    load, wind_u, solar_u = _make_data(24)
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
    opt = CapacityOptimizer(storage_params=sp, cost_params=cost, search_params=search)
    with pytest.raises(ValueError, match='feasible'):
        opt.optimize(load, wind_u, solar_u, green_ratio_min=0.99, self_use_ratio_min=0.99)
