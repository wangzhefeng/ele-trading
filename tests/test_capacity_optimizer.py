from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ele_trading.capacity_planning.capacity_optimizer import (
    CapacityOptimizer, CapacityPlanResult, simulate_operation,
)

_STORAGE = {
    'eta_charge': 0.95, 'eta_discharge': 0.95,
    'dod': 0.90, 'soc_min': 0.10, 'c_rate': 0.5,
}
_COST = {
    'wind_yuan_per_kw': 5000,
    'pv_yuan_per_kw': 3500,
    'ess_yuan_per_kwh': 1500,
}

def _make_series(n=8760, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range('2023-01-01', periods=n, freq='h')
    load = pd.Series(rng.uniform(5, 10, n), index=idx)
    wind_u = pd.Series(rng.uniform(0, 0.4, n), index=idx)
    solar_u = pd.Series(rng.uniform(0, 0.3, n), index=idx)
    return load, wind_u, solar_u


def test_simulate_operation_returns_expected_keys():
    load, wind_u, solar_u = _make_series(100)
    result = simulate_operation(load, wind_u, solar_u, 5.0, 5.0, 10.0, _STORAGE)
    for key in ('green_ratio', 'self_use_ratio', 'curtailment_ratio',
                'total_green_gen_mwh', 'total_grid_buy_mwh', 'total_curtailment_mwh'):
        assert key in result


def test_simulate_operation_ratios_in_range():
    load, wind_u, solar_u = _make_series(100)
    result = simulate_operation(load, wind_u, solar_u, 5.0, 5.0, 10.0, _STORAGE)
    assert 0.0 <= result['green_ratio'] <= 1.0
    assert 0.0 <= result['self_use_ratio'] <= 1.0
    assert 0.0 <= result['curtailment_ratio'] <= 1.0


def test_simulate_no_curtailment_when_undersized():
    load, wind_u, solar_u = _make_series(100)
    result = simulate_operation(load, wind_u, solar_u, 0.1, 0.1, 0.0, _STORAGE)
    assert result['curtailment_ratio'] < 1e-9


def test_capacity_optimizer_returns_correct_type():
    opt = CapacityOptimizer(_STORAGE, _COST)
    load, wind_u, solar_u = _make_series(100)
    result = opt.optimize(load, wind_u, solar_u,
                          green_ratio_min=0.1, self_use_ratio_min=0.1)
    assert isinstance(result, CapacityPlanResult)


def test_capacity_optimizer_respects_green_ratio():
    opt = CapacityOptimizer(_STORAGE, _COST, {'coarse_step_mw': 5, 'coarse_step_mwh': 10})
    load, wind_u, solar_u = _make_series(100)
    result = opt.optimize(load, wind_u, solar_u,
                          green_ratio_min=0.2, self_use_ratio_min=0.1)
    assert result.green_ratio >= 0.2


def test_capacity_optimizer_respects_self_use_ratio():
    opt = CapacityOptimizer(_STORAGE, _COST, {'coarse_step_mw': 5, 'coarse_step_mwh': 10})
    load, wind_u, solar_u = _make_series(100)
    result = opt.optimize(load, wind_u, solar_u,
                          green_ratio_min=0.1, self_use_ratio_min=0.5)
    assert result.self_use_ratio >= 0.5


def test_capacity_optimizer_fixed_pv():
    opt = CapacityOptimizer(_STORAGE, _COST, {'coarse_step_mw': 5, 'coarse_step_mwh': 10})
    load, wind_u, solar_u = _make_series(100)
    result = opt.optimize(load, wind_u, solar_u,
                          green_ratio_min=0.1, self_use_ratio_min=0.1,
                          fixed_pv_mw=10.0)
    assert result.pv_mw == 10.0


def test_capacity_optimizer_fixed_wind():
    opt = CapacityOptimizer(_STORAGE, _COST, {'coarse_step_mw': 5, 'coarse_step_mwh': 10})
    load, wind_u, solar_u = _make_series(100)
    result = opt.optimize(load, wind_u, solar_u,
                          green_ratio_min=0.1, self_use_ratio_min=0.1,
                          fixed_wind_mw=10.0)
    assert result.wind_mw == 10.0


def test_capacity_optimizer_nonfeasible_raises():
    opt = CapacityOptimizer(_STORAGE, _COST, {
        'coarse_step_mw': 5, 'coarse_step_mwh': 10,
        'max_wind_mw': 1, 'max_pv_mw': 1, 'max_ess_mwh': 1,
    })
    load, wind_u, solar_u = _make_series(100)
    with pytest.raises(ValueError, match='No feasible'):
        opt.optimize(load, wind_u, solar_u,
                     green_ratio_min=0.99, self_use_ratio_min=0.99)
