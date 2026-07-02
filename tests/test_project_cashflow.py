"""Item 2 tests: 逐年项目现金流模型（build_project_cashflows）。"""
from __future__ import annotations

import pytest

from ele_trading.capacity_planning.irr_finance import (
    ReplacementEvent,
    build_project_cashflows,
    compute_npv,
)


def test_cashflow_degenerates_to_levelized_when_no_extras():
    from ele_trading.capacity_planning.irr_finance import evaluate_levelized_irr

    base = evaluate_levelized_irr(
        total_capex_yuan=1000, annual_revenue_yuan=200, annual_opex_yuan=20, life_years=10
    )
    proj = build_project_cashflows(
        capex_yuan=1000, annual_revenue_y1_yuan=200, annual_opex_y1_yuan=20, life_years=10
    )  # 无税/无更换/无残值/无衰减
    assert proj.irr == pytest.approx(base.irr)


def test_replacement_lowers_irr():
    no_rep = build_project_cashflows(
        capex_yuan=1000, annual_revenue_y1_yuan=300, annual_opex_y1_yuan=30, life_years=15
    )
    with_rep = build_project_cashflows(
        capex_yuan=1000,
        annual_revenue_y1_yuan=300,
        annual_opex_y1_yuan=30,
        life_years=15,
        replacements=[ReplacementEvent(year=10, cost_yuan=300)],
    )
    assert with_rep.irr < no_rep.irr


def test_salvage_raises_irr_and_tax_lowers_it():
    base = build_project_cashflows(
        capex_yuan=1000, annual_revenue_y1_yuan=300, annual_opex_y1_yuan=30, life_years=15
    )
    salvage = build_project_cashflows(
        capex_yuan=1000,
        annual_revenue_y1_yuan=300,
        annual_opex_y1_yuan=30,
        life_years=15,
        salvage_ratio=0.1,
    )
    taxed = build_project_cashflows(
        capex_yuan=1000,
        annual_revenue_y1_yuan=300,
        annual_opex_y1_yuan=30,
        life_years=15,
        tax_rate=0.25,
    )
    assert salvage.irr > base.irr > taxed.irr


def test_npv_and_payback_only_when_discount_given():
    proj = build_project_cashflows(
        capex_yuan=1000,
        annual_revenue_y1_yuan=300,
        annual_opex_y1_yuan=30,
        life_years=10,
        discount_rate=0.08,
    )
    assert proj.npv_yuan is not None and proj.payback_year is not None
    # NPV 计算可用且为数值（discount_rate=0.08）
    assert isinstance(compute_npv(proj.cashflows, 0.08), float)
    proj2 = build_project_cashflows(
        capex_yuan=1000, annual_revenue_y1_yuan=300, annual_opex_y1_yuan=30, life_years=10
    )
    assert proj2.npv_yuan is None and proj2.payback_year is None
