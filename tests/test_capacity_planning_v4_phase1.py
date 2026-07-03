"""V4 phase-1 capacity-planning behavior tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def test_capacity_planning_import_does_not_require_cvxpy():
    script = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "cvxpy" or name.startswith("cvxpy."):
        raise ImportError("blocked cvxpy for lazy import test")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
import ele_trading.capacity_planning as cp
assert cp.plan_wind_pv_bess_for_target_irr
assert cp.solve_capacity_sizing
print("OK")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OK"


def test_canonical_dispatch_24h_oracle_conserves_energy_and_soc():
    from ele_trading.capacity_planning.models.canonical_dispatch import canonical_dispatch
    from ele_trading.capacity_planning.models.physics_contract import BESSPhysicsContract

    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    load_kw = np.full(24, 100.0)
    solar_kw = np.array([0.0] * 6 + [200.0] * 6 + [0.0] * 12)
    bess = BESSPhysicsContract(eta_charge=1.0, eta_discharge=1.0, soc_init_frac=0.0, soc_min_frac=0.0)

    result = canonical_dispatch(
        load_kw=load_kw,
        generation_kw={"pv": solar_kw},
        bess=bess,
        bess_capacity_kwh=300.0,
        timestamps=idx,
        dt_hours=1.0,
    )

    assert result.annual_summary["generation_kwh"] == pytest.approx(1200.0)
    assert result.annual_summary["charge_kwh"] == pytest.approx(300.0)
    assert result.annual_summary["discharge_kwh"] == pytest.approx(300.0)
    assert result.annual_summary["curtail_kwh"] == pytest.approx(300.0)
    assert result.annual_summary["grid_buy_kwh"] == pytest.approx(1500.0)
    assert result.soc_kwh.max() == pytest.approx(300.0)
    assert result.soc_kwh[-1] == pytest.approx(0.0)

    np.testing.assert_allclose(
        result.generation_kwh,
        result.direct_used_kwh + result.charge_kwh + result.curtail_kwh,
    )
    np.testing.assert_allclose(
        result.load_kwh,
        result.direct_used_kwh + result.discharge_kwh + result.grid_buy_kwh,
    )


def test_monthly_settlement_sums_to_time_step_totals_and_uses_peak_net_load():
    from ele_trading.capacity_planning.models.canonical_dispatch import canonical_dispatch
    from ele_trading.capacity_planning.models.physics_contract import BESSPhysicsContract
    from ele_trading.capacity_planning.settlement import settle_monthly

    idx = pd.date_range("2026-01-31 22:00:00", periods=4, freq="h")
    load_kw = np.array([100.0, 100.0, 100.0, 100.0])
    generation_kw = np.array([100.0, 200.0, 0.0, 0.0])
    dispatch = canonical_dispatch(
        load_kw=load_kw,
        generation_kw={"pv": generation_kw},
        bess=BESSPhysicsContract(eta_charge=1.0, eta_discharge=1.0, soc_init_frac=0.0, soc_min_frac=0.0),
        bess_capacity_kwh=100.0,
        timestamps=idx,
        dt_hours=1.0,
    )

    settlement = settle_monthly(
        dispatch,
        green_price_yuan_per_kwh=0.32,
        ppa_price_yuan_per_kwh=0.246,
        grid_buy_price_yuan_per_kwh=0.36,
        baseline_price_yuan_per_kwh=0.36,
        demand_charge_yuan_per_kw=10.0,
    )

    assert settlement.annual_summary["green_used_kwh"] == pytest.approx(dispatch.annual_summary["green_used_kwh"])
    assert settlement.annual_summary["grid_buy_kwh"] == pytest.approx(dispatch.annual_summary["grid_buy_kwh"])
    assert settlement.annual_summary["ppa_revenue_yuan"] == pytest.approx(0.246 * dispatch.annual_summary["green_used_kwh"])
    assert settlement.annual_summary["demand_charge_yuan"] == pytest.approx(100.0 * 10.0)
    assert [row.month for row in settlement.monthly] == ["2026-01", "2026-02"]


def test_dispatch_annual_python_fallback_simulates_bess_when_numba_disabled():
    from ele_trading.capacity_planning.models.dispatch_algo import dispatch_annual
    from ele_trading.capacity_planning.wind_pv_bess_irr_planner import WindPVBESSIRRPlanConfig

    load = np.array([100.0, 100.0, 100.0, 100.0])
    pv = np.array([200.0, 200.0, 0.0, 0.0])
    other = np.zeros_like(load)
    cfg = WindPVBESSIRRPlanConfig(
        eta_roundtrip=1.0,
        c_rate=1.0,
        soc_init_frac=0.0,
        soc_min_frac=0.0,
        use_numba=False,
    )

    result = dispatch_annual(load, other, pv, other, 100.0, 1.0, cfg)

    assert result["bess_discharge_kwh"] == pytest.approx(100.0)
    assert result["ren_used_kwh"] == pytest.approx(300.0)
    assert result["curtail_kwh"] == pytest.approx(100.0)


def test_wind_pv_bess_irr_runner_requires_explicit_data_dir_or_demo():
    from app.capacity_planning.run_wind_pv_bess_irr_planning import _resolve_data_dir

    with pytest.raises(SystemExit):
        _resolve_data_dir(data_dir=None, demo=False)

    demo_dir = _resolve_data_dir(data_dir=None, demo=True)
    assert demo_dir.parts[-4:] == ("data", "profit_calc", "wind_pv_bess", "v1")

    explicit = _resolve_data_dir(data_dir=Path("/tmp/case-input"), demo=False)
    assert explicit == Path("/tmp/case-input")
