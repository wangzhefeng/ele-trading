import pytest

from ele_trading.evaluation.metrics import compute_irr
from investment_estimation.todo.irr_finance import (
    backsolve_green_ppa_price,
    compute_target_irr_gap_metrics,
    evaluate_degraded_irr,
    evaluate_equity_irr,
    evaluate_levelized_irr,
    required_levelized_cashflow,
)
from investment_estimation.todo.pv_bess_irr_planner import (
    PVBESSIRRConfig,
    scan_pv_bess_irr,
)
from investment_estimation.todo.wind_bess_irr_planner import (
    WindBESSIRRConfig,
    scan_wind_bess_irr,
)
from investment_estimation.todo.irr_calculation import IRRCalculator


def test_evaluate_levelized_irr_builds_equal_annual_cashflows():
    result = evaluate_levelized_irr(
        total_capex_yuan=1000.0,
        annual_revenue_yuan=260.0,
        annual_opex_yuan=10.0,
        life_years=4,
    )

    expected_cashflows = [-1000.0, 250.0, 250.0, 250.0, 250.0]
    assert result.annual_cashflow_yuan == pytest.approx(250.0)
    assert result.cashflows == expected_cashflows
    assert result.irr == pytest.approx(compute_irr(expected_cashflows))


def test_evaluate_equity_irr_uses_project_finance_model():
    result = evaluate_equity_irr(
        wind_mw=1.0,
        pv_mw=0.0,
        bess_mwh=0.0,
        wind_capex_yuan_per_kw=1000.0,
        pv_capex_yuan_per_kwp=1000.0,
        bess_capex_yuan_per_kwh=1000.0,
        annual_revenue_yuan=300_000.0,
        annual_opex_yuan=10_000.0,
        life_years=5,
        loan_rate=0.03,
        loan_term=5,
    )

    assert result.total_capex_yuan > 1_000_000.0
    assert result.annual_revenue_yuan == pytest.approx(300_000.0)
    assert result.annual_opex_yuan == pytest.approx(10_000.0)
    assert result.irr == result.irr_eq_post
    assert result.irr_ti_pre is not None
    assert result.irr_ti_post is not None
    assert result.irr_eq_pre is not None
    assert result.irr_eq_post is not None
    assert len(result.cashflows_wan) == 7


def test_evaluate_equity_irr_preserves_two_year_construction_cashflows():
    result = evaluate_equity_irr(
        wind_mw=1.0,
        pv_mw=0.0,
        bess_mwh=0.0,
        wind_capex_yuan_per_kw=1000.0,
        pv_capex_yuan_per_kwp=1000.0,
        bess_capex_yuan_per_kwh=1000.0,
        annual_revenue_yuan=300_000.0,
        annual_opex_yuan=10_000.0,
        life_years=5,
        loan_rate=0.03,
        loan_term=5,
    )

    calc = IRRCalculator(
        wind_capacity=1.0,
        solar_capacity=0.0,
        storage_capacity=0.0,
        wind_unit_cost=1.0,
        solar_unit_cost=1.0,
        storage_unit_cost=1.0,
        operating_years=5,
        construction_years=2,
        loan_rate=0.03,
        loan_term=5,
        external_revenue=300_000.0 / 1.13 / 10000.0,
        external_opex=10_000.0 / 10000.0,
        delivery_cost=0.0,
        survey_unit_cost=0.0,
        other_unit_cost=0.0,
    )
    expected = calc.run()

    assert result.cashflows_wan == pytest.approx([float(x) for x in expected["eq_post"]])
    assert result.cashflows_wan[1] < 0.0


def test_evaluate_equity_irr_propagates_none_when_irr_unresolved():
    """现金流全负（IRR 无解）时，irr / irr_eq_post 应透传 None，而非伪 0.0。"""
    result = evaluate_equity_irr(
        wind_mw=1.0,
        pv_mw=0.0,
        bess_mwh=0.0,
        wind_capex_yuan_per_kw=1000.0,
        pv_capex_yuan_per_kwp=1000.0,
        bess_capex_yuan_per_kwh=1000.0,
        annual_revenue_yuan=1_000.0,        # 极低含税收入
        annual_opex_yuan=50_000_000.0,      # 极高运维 → 运营期净现金流为负
        life_years=5,
        loan_rate=0.03,
        loan_term=5,
    )

    assert result.irr is None
    assert result.irr_eq_post is None
    assert result.irr_eq_pre is None
    assert result.irr_ti_pre is None
    assert result.irr_ti_post is None


def test_evaluate_equity_irr_reports_none_when_equity_cashflow_has_no_irr():
    """亏损项目（资本金现金流全周期为负、无实数 IRR）应返回 None，而非伪 ~0.5 IRR。

    回归缺陷：irr_calculation 的牛顿法对无实根现金流会停在 NPV 残差巨大的伪根上，
    irr_robust 又按 |NPV| 取最小者，使亏损方案被误报为 ~50% 资本金 IRR，进而被
    minimum 模式当成"达标可行解"选中。复现自 wind_pv_bess 最优解 (103MW 风 + 138MW 光)。
    """
    result = evaluate_equity_irr(
        wind_mw=103.0,
        pv_mw=138.0,
        bess_mwh=0.0,
        wind_capex_yuan_per_kw=5000.0,
        pv_capex_yuan_per_kwp=3500.0,
        bess_capex_yuan_per_kwh=800.0,
        annual_revenue_yuan=90_602_512.90,   # = PPA 0.18880 × green_used 4.7999e8
        annual_opex_yuan=19_960_000.0,        # = 设备投资 9.98e8 × 2%
        life_years=15,
        construction_years=2,
        loan_rate=0.03,
        vat_rate=0.13,
    )

    # 资本金税后现金流全周期加总为负 → 不存在实数 IRR
    assert sum(result.cashflows_wan) < 0
    assert result.irr is None
    assert result.irr_eq_post is None
    assert result.irr_eq_pre is None


def test_irr_solver_rejects_spurious_root_for_no_irr_cashflow():
    """irr_calculation 的求解器对无实根现金流必须返回 None，不得返回伪根。

    回归：旧实现在该 eq_post 现金流上由 guess=0.30 收敛到 ~0.501，
    但该处 NPV 残差 ~1.97e4 万元（远非 0），属伪根。
    """
    from investment_estimation.todo.irr_calculation import compute_irr as newton_irr
    from investment_estimation.todo.irr_calculation import irr_robust

    calc = IRRCalculator(
        wind_capacity=103.0,
        solar_capacity=138.0,
        storage_capacity=0.0,
        wind_unit_cost=5.0,
        solar_unit_cost=3.5,
        storage_unit_cost=0.8,
        operating_years=15,
        construction_years=2,
        loan_rate=0.03,
        loan_term=15,
        external_revenue=90_602_512.90 / 1.13 / 10000.0,
        external_opex=19_960_000.0 / 10000.0,
        delivery_cost=0.0,
        survey_unit_cost=0.0,
        other_unit_cost=0.0,
    )
    eq_post = [float(x) for x in calc.run()["eq_post"]]

    assert sum(eq_post) < 0                      # 全周期净负 → 无实数 IRR
    assert newton_irr(eq_post, guess=0.30) is None
    assert irr_robust(eq_post) is None


def test_irr_calculator_single_construction_year_deploys_full_investment():
    """construction_years=1 时，建设投资与流动资金应在第 0 年一次性全额投出（不漏投）。

    回归缺陷 2：旧逻辑 nc=1 仅投 95% 且流动资金从不投出却被末年回收，导致 IRR 虚高。
    """
    common = dict(
        wind_capacity=1.0, solar_capacity=0.0, storage_capacity=0.0,
        wind_unit_cost=0.05, solar_unit_cost=0.05, storage_unit_cost=0.05,
        operating_years=15, loan_rate=0.03, loan_term=15,
        external_revenue=0.246 * 24000 / 1.13 / 10000.0, external_opex=0.0,
        delivery_cost=0.0, survey_unit_cost=0.0, other_unit_cost=0.0,
    )
    calc1 = IRRCalculator(construction_years=1, **common)
    calc2 = IRRCalculator(construction_years=2, **common)
    r1, r2 = calc1.run(), calc2.run()

    const_inv = 1.0 * 0.05 * 100  # wind_inv (万元)，delivery/survey/connection 均为 0
    eq_construction = const_inv * 0.20
    wc = const_inv * 0.006

    # nc=1：第 0 年一次性投出全部资本金(eq_construction + wc)，不再仅投 95%
    assert r1["eq_post"][0] == pytest.approx(-(eq_construction + wc))
    # nc=2：两年合计同样全额投出
    assert (abs(r2["eq_post"][0]) + abs(r2["eq_post"][1])) == pytest.approx(eq_construction + wc)
    # 两口径均收敛到有限 IRR（nc=1 因更早发电略高，属正常，非虚高）
    assert r1["irr_eq_post"] is not None and r2["irr_eq_post"] is not None


def test_backsolve_green_ppa_price_matches_owner_average_price():
    result = backsolve_green_ppa_price(
        load_kwh=24_000.0,
        green_used_kwh=24_000.0,
        target_owner_price_yuan_per_kwh=0.32,
        grid_buy_price_yuan_per_kwh=0.36,
        green_price_adder_yuan_per_kwh=0.074,
    )

    assert result.annual_grid_buy_kwh == pytest.approx(0.0)
    assert result.green_price_yuan_per_kwh == pytest.approx(0.32)
    assert result.ppa_price_yuan_per_kwh == pytest.approx(0.246)
    assert result.owner_avg_price_yuan_per_kwh == pytest.approx(0.32)


def test_backsolve_green_ppa_price_rejects_zero_green_usage():
    with pytest.raises(ValueError, match="green_used_kwh"):
        backsolve_green_ppa_price(
            load_kwh=100.0,
            green_used_kwh=0.0,
            target_owner_price_yuan_per_kwh=0.32,
            grid_buy_price_yuan_per_kwh=0.36,
            green_price_adder_yuan_per_kwh=0.074,
        )


def test_required_levelized_cashflow_uses_annuity_formula():
    assert required_levelized_cashflow(
        total_capex_yuan=1000.0,
        target_irr=0.0,
        life_years=5,
    ) == pytest.approx(200.0)

    expected = 1000.0 * 0.1 / (1.0 - (1.0 + 0.1) ** -5)
    assert required_levelized_cashflow(
        total_capex_yuan=1000.0,
        target_irr=0.1,
        life_years=5,
    ) == pytest.approx(expected)


def test_compute_target_irr_gap_metrics_derives_required_prices_and_capex_gap():
    result = compute_target_irr_gap_metrics(
        total_capex_yuan=1000.0,
        annual_cashflow_yuan=120.0,
        annual_opex_yuan=30.0,
        green_used_kwh=500.0,
        grid_buy_kwh=100.0,
        target_irr=0.08,
        life_years=10,
        grid_buy_price_yuan_per_kwh=0.36,
        green_price_adder_yuan_per_kwh=0.074,
        target_owner_price_yuan_per_kwh=0.32,
    )

    required_cf = required_levelized_cashflow(
        total_capex_yuan=1000.0,
        target_irr=0.08,
        life_years=10,
    )
    assert result.required_annual_cashflow_yuan == pytest.approx(required_cf)
    assert result.annual_cashflow_gap_yuan == pytest.approx(required_cf - 120.0)
    assert result.required_green_price_yuan_per_kwh == pytest.approx((required_cf + 30.0) / 500.0)
    assert result.required_ppa_price_yuan_per_kwh == pytest.approx(result.required_green_price_yuan_per_kwh - 0.074)
    assert result.capex_reduction_needed_yuan > 0.0


def test_evaluate_degraded_irr_builds_capacity_decay_cashflows():
    result = evaluate_degraded_irr(
        capex_yuan=1000.0,
        annual_revenue_y1_yuan=100.0,
        annual_opex_y1_yuan=10.0,
        life_years=3,
        capacity_end_ratio=0.8,
    )

    assert result.annual_revenues_yuan == pytest.approx([100.0, 90.0, 80.0])
    assert result.annual_opexes_yuan == pytest.approx([10.0, 9.0, 8.0])
    assert result.cashflows == pytest.approx([-1000.0, 90.0, 81.0, 72.0])
    assert result.irr == pytest.approx(compute_irr(result.cashflows))
    assert result.life_revenue_yuan == pytest.approx(270.0)
    assert result.life_net_yuan == pytest.approx(243.0)


def test_pv_bess_irr_scan_keeps_existing_cashflow_and_rounding_contract():
    import pandas as pd

    df = pd.DataFrame([{"PV": 10.0, "Load": 8.0, "Curtail": 2.0}])
    cfg = PVBESSIRRConfig(
        pv_capex_yuan=1000.0,
        bess_capex_per_kwh=100.0,
        export_price_per_kwh=0.2,
        max_export_ratio=0.2,
        life_years=2,
        platform_fee_yuan_per_year=10.0,
        o_and_m_per_kwh=0.01,
    )

    result = scan_pv_bess_irr(df, bess_range=[1.0], buy_price_range=[0.5], cfg=cfg)
    row = result.best

    assert row is not None
    assert row.annual_revenue_yuan == pytest.approx(4_400.0)
    assert row.annual_energy_mwh == pytest.approx(10.0)
    assert row.annual_om_yuan == pytest.approx(100.0)
    assert row.annual_cf_yuan == pytest.approx(4_290.0)
    assert row.total_capex_yuan == pytest.approx(101_000.0)


def test_wind_bess_irr_scan_keeps_existing_cashflow_and_rounding_contract():
    import pandas as pd

    df = pd.DataFrame([{"Wind": 10.0, "Load": 8.0, "Curtail": 2.0}])
    cfg = WindBESSIRRConfig(
        wind_capex_yuan=1000.0,
        bess_capex_per_kwh=100.0,
        export_price_per_kwh=0.2,
        max_export_ratio=0.2,
        life_years=2,
        platform_fee_yuan_per_year=10.0,
        o_and_m_per_kwh=0.01,
    )

    result = scan_wind_bess_irr(df, bess_range=[1.0], buy_price_range=[0.5], cfg=cfg)
    row = result.best

    assert row is not None
    assert row.annual_revenue_yuan == pytest.approx(4_400.0)
    assert row.annual_energy_mwh == pytest.approx(10.0)
    assert row.annual_om_yuan == pytest.approx(100.0)
    assert row.annual_cf_yuan == pytest.approx(4_290.0)
    assert row.total_capex_yuan == pytest.approx(101_000.0)
