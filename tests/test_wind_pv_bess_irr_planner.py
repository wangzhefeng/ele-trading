"""IRR 目标型 Wind+PV+BESS 容量规划测试。"""

import numpy as np
import pandas as pd
import pytest

from ele_trading.capacity_planning.wind_pv_bess_planner import (
    WindPVBESSPlanConfig,
    dispatch_annual,
    evaluate_fixed_wind_pv_bess_capacity,
)
from ele_trading.capacity_planning.wind_pv_bess_irr_planner import (
    WindPVBESSIRRPlanConfig,
    WindPVBESSIRRResult,
    plan_wind_pv_bess_for_target_irr,
)


def _flat_case(hours: int = 24, load_kw: float = 1000.0):
    idx = pd.date_range("2026-01-01", periods=hours, freq="h")
    df_load = pd.DataFrame({"Time": idx, "P_kw": load_kw})
    wind_unit = pd.Series(1000.0, index=idx, name="wind_unit_kw")
    pv_unit = pd.Series(1.0, index=idx, name="pv_unit_kw")
    return df_load, wind_unit, pv_unit


def test_fixed_capacity_helper_matches_dispatch_engine():
    """固定容量 helper 应复用现有年度调度口径。"""
    df_load, wind_unit, pv_unit = _flat_case(hours=4, load_kw=900.0)
    cfg = WindPVBESSPlanConfig(enable_gate_check=False, use_numba=False)

    result = evaluate_fixed_wind_pv_bess_capacity(
        df_load,
        wind_unit_kw=wind_unit,
        pv_unit_kw=pv_unit,
        wind_mw=0.5,
        pv_mw=0.4,
        bess_mwh=0.2,
        cfg=cfg,
    )

    load = np.full(4, 900.0)
    wind = np.full(4, 500.0)
    pv = np.full(4, 400.0)
    other = np.zeros(4)
    expected = dispatch_annual(load, wind, pv, other, 200.0, 1.0, cfg)

    assert result["ren_gen_kwh"] == pytest.approx(expected["ren_gen_kwh"])
    assert result["ren_used_kwh"] == pytest.approx(expected["ren_used_kwh"])
    assert result["load_kwh"] == pytest.approx(expected["load_kwh"])
    assert result["curtail_kwh"] == pytest.approx(expected["curtail_kwh"])


def test_owner_price_and_ppa_are_back_solved_from_green_usage():
    """业主综合电价应由绿电价格和电网购电共同回算到目标 0.32。"""
    df_load, wind_unit, pv_unit = _flat_case()
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
    )

    result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)

    assert result.status == "ok"
    assert result.green_price == pytest.approx(0.32)
    assert result.ppa_price == pytest.approx(0.246)
    assert result.owner_avg_price == pytest.approx(0.32)
    assert result.annual_revenue_yuan == pytest.approx(result.green_price * result.annual_green_used_kwh)


def test_negative_ppa_candidates_are_filtered():
    """反推 PPA <= 0 的物理可行组合不应进入可选结果。"""
    df_load, wind_unit, pv_unit = _flat_case()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=0.1,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        wind_step_mw=0.1,
        pv_step_mw=1.0,
        bess_step_mwh=1.0,
        load_cover_ratio_min=0.05,
        target_irr=0.0,
        irr_tolerance=1.0,
        wind_capex_yuan_per_kw=1.0,
        annual_opex_ratio=0.0,
    )

    result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)

    assert result.status == "no_solution"
    assert result.diagnostics is not None
    assert result.diagnostics["reason"].iloc[0] == "non_positive_ppa"


def test_best_solution_prefers_lowest_capex_within_irr_tolerance():
    """多个组合满足 IRR 容差时，应选择总投资最低的组合。"""
    df_load, wind_unit, pv_unit = _flat_case()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=1.0,
        pv_max_mw=1.0,
        bess_max_mwh=0.0,
        wind_step_mw=1.0,
        pv_step_mw=1.0,
        bess_step_mwh=1.0,
        target_irr=0.0,
        irr_tolerance=10.0,
        wind_capex_yuan_per_kw=10.0,
        pv_capex_yuan_per_kwp=1.0,
        annual_opex_ratio=0.0,
    )

    result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)

    assert result.status == "ok"
    assert result.wind_mw == pytest.approx(0.0)
    assert result.pv_mw == pytest.approx(1.0)


def test_no_solution_returns_nearest_irr_diagnostics():
    """没有 IRR 命中解时，应返回最接近目标 IRR 的候选诊断。"""
    df_load, wind_unit, pv_unit = _flat_case()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=1.0,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        wind_step_mw=1.0,
        pv_step_mw=1.0,
        bess_step_mwh=1.0,
        target_irr=0.99,
        irr_tolerance=0.0001,
        wind_capex_yuan_per_kw=1.0,
        annual_opex_ratio=0.0,
    )

    result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)

    assert isinstance(result, WindPVBESSIRRResult)
    assert result.status == "no_solution"
    assert result.diagnostics is not None
    assert result.diagnostics.iloc[0]["irr_gap"] == pytest.approx(
        abs(result.diagnostics.iloc[0]["irr"] - cfg.target_irr)
    )
