"""Item 3 tests: SearchObjective 多目标 + PPA 锁定正向 IRR.

Task 6 先放财务接入测试；Task 7/8 追加目标函数与 PPA 锁定测试。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _base_inputs():
    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    df_load = pd.DataFrame({"Time": idx, "P_kw": 1000.0})
    wind_unit = pd.Series(1000.0, index=idx, name="wind_unit_kw")
    pv_unit = pd.Series(0.0, index=idx, name="pv_unit_kw")
    return df_load, wind_unit, pv_unit


# ---------------------------------------------------------------------------
# Task 6: 主 IRR 链接入 build_project_cashflows（储能更换）
# ---------------------------------------------------------------------------


def test_irr_planner_uses_project_cashflow_with_replacement():
    from ele_trading.capacity_planning.wind_pv_bess_irr_planner import (
        WindPVBESSIRRPlanConfig,
        plan_wind_pv_bess_for_target_irr,
    )

    df_load, wind_unit, pv_unit = _base_inputs()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=1.0,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        wind_step_mw=1.0,
        pv_step_mw=1.0,
        bess_step_mwh=1.0,
        target_irr=0.0,
        irr_tolerance=10.0,
        wind_capex_yuan_per_kw=1.0,
        annual_opex_ratio=0.0,
        replacement_year=10,
        replacement_cost_yuan=50.0,
    )
    r = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)
    assert r.status == "ok"
    assert r.best_solution["replacement_events_yuan"][9] == 50.0  # 第10年更换


# ---------------------------------------------------------------------------
# Task 7: SearchObjective 多目标选择
# ---------------------------------------------------------------------------


def test_maximize_irr_picks_highest_irr_candidate():
    from ele_trading.capacity_planning.wind_pv_bess_irr_planner import (
        WindPVBESSIRRPlanConfig,
        plan_wind_pv_bess_for_target_irr,
    )

    df_load, wind_unit, pv_unit = _base_inputs()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=5.0,
        wind_step_mw=1.0,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        bess_step_mwh=1.0,
        target_irr=0.0,
        irr_tolerance=10.0,
        wind_capex_yuan_per_kw=1.0,
        annual_opex_ratio=0.0,
        objective="maximize_irr",
    )
    r = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)
    assert r.status == "ok"
    # MAXIMIZE_IRR 下 best 的 irr 应等于 diagnostics 中所有 ok 候选的最大 irr
    ok = r.diagnostics[r.diagnostics["reason"] == "ok"] if r.diagnostics is not None else None
    if ok is not None and len(ok):
        assert r.irr == pytest.approx(ok["irr"].max())


# ---------------------------------------------------------------------------
# Task 8: PPA 锁定 → 正向求 IRR
# ---------------------------------------------------------------------------


def test_ppa_locked_forward_irr_does_not_backsolve():
    from ele_trading.capacity_planning.wind_pv_bess_irr_planner import (
        WindPVBESSIRRPlanConfig,
        plan_wind_pv_bess_for_target_irr,
    )

    df_load, wind_unit, pv_unit = _base_inputs()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=1.0,
        wind_step_mw=1.0,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        bess_step_mwh=1.0,
        target_irr=0.0,
        irr_tolerance=10.0,
        wind_capex_yuan_per_kw=1.0,
        annual_opex_ratio=0.0,
        ppa_price_locked=0.30,
        green_price_adder_yuan_per_kwh=0.074,
    )
    r = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)
    assert r.status == "ok"
    assert r.ppa_price == pytest.approx(0.30)  # 用锁定价，未反推
    assert r.green_price == pytest.approx(0.30 + 0.074)  # green = ppa + adder


def test_ppa_locked_takes_precedence_over_target_owner_price():
    # target_owner_price 默认 0.32 无法区分是否显式设置 → 用"locked 优先"而非硬互斥
    from ele_trading.capacity_planning.wind_pv_bess_irr_planner import (
        WindPVBESSIRRPlanConfig,
        plan_wind_pv_bess_for_target_irr,
    )

    df_load, wind_unit, pv_unit = _base_inputs()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=1.0,
        wind_step_mw=1.0,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        bess_step_mwh=1.0,
        target_irr=0.0,
        irr_tolerance=10.0,
        wind_capex_yuan_per_kw=1.0,
        annual_opex_ratio=0.0,
        ppa_price_locked=0.30,
        green_price_adder_yuan_per_kwh=0.074,
        objective="maximize_irr",
    )
    r = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)
    assert r.status == "ok"
    assert r.ppa_price == pytest.approx(0.30)  # locked 优先，未走 target_owner_price 反推
