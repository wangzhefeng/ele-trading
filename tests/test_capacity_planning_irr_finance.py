import pytest

from ele_trading.evaluation.metrics import compute_irr
from ele_trading.capacity_planning.irr_finance import (
    backsolve_green_ppa_price,
    compute_target_irr_gap_metrics,
    evaluate_degraded_irr,
    evaluate_levelized_irr,
    required_levelized_cashflow,
)
from ele_trading.capacity_planning.pv_bess_irr_planner import (
    PVBESSIRRConfig,
    scan_pv_bess_irr,
)
from ele_trading.capacity_planning.wind_bess_irr_planner import (
    WindBESSIRRConfig,
    scan_wind_bess_irr,
)


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
