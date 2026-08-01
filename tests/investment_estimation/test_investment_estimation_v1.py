from __future__ import annotations

from pathlib import Path
from dataclasses import replace

import pandas as pd
import pytest

from investment_estimation.capacity_search import run_capacity_search
from investment_estimation.config_loader import (
    BESSConfig,
    BaselineProjectConfig,
    CapacitySearchConfig,
    CaseConfig,
    FinanceConfig,
    PathConfig,
    ProjectConfig,
    load_case_config,
)
from investment_estimation.app.run_capacity_search import run_search_from_yaml
from investment_estimation.app.run_mvp_demo import run_case_from_yaml
from investment_estimation.data_provider import (
    build_timeseries,
    generate_sample_csvs,
    read_load_csv,
    read_price_csv,
    read_resource_csv,
    validate_timeseries,
)
from investment_estimation.finance import annual_cashflow_table, compute_npv, compute_payback_years


def test_v1_yaml_loads_search_and_settlement_config() -> None:
    case = load_case_config("src/investment_estimation/configs/v1_capacity_search_demo.yaml")

    assert case.search.enabled is True
    assert case.search.wind_capacity_kw == (800.0, 1000.0)
    assert case.search.min_owner_saving_pct == 0.05
    assert case.project.settlement.basic_charge_per_month == 1000.0
    assert case.paths.candidate_output_csv is not None


def test_data_validation_rejects_duplicate_time(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-01-01 00:00:00", "2026-01-01 00:00:00"]),
            "load_kw": [1.0, 2.0],
            "price": [0.5, 0.5],
            "price_type": ["flat", "flat"],
            "pv_kw": [0.0, 0.0],
            "wind_kw": [0.0, 0.0],
            "dt_hours": [1.0, 1.0],
        }
    )

    with pytest.raises(ValueError, match="Duplicate timestamps"):
        validate_timeseries(df)


def test_price_csv_normalizes_chinese_price_type(tmp_path: Path) -> None:
    price_csv = tmp_path / "price.csv"
    pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=5, freq="1h"),
            "price": [0.2, 0.3, 0.6, 0.9, 1.2],
            "price_type": ["深谷", "谷", "平", "高峰", "尖峰"],
        }
    ).to_csv(price_csv, index=False)

    df = read_price_csv(price_csv)

    assert df["price_type"].tolist() == ["deep_valley", "valley", "flat", "peak", "sharp_peak"]


def test_yaml_loader_normalizes_bess_price_type_aliases() -> None:
    case = load_case_config("src/investment_estimation/configs/mvp_demo.yaml")

    assert case.project.bess.charge_price_types == ("valley", "flat")
    assert case.project.bess.discharge_price_types == ("peak", "sharp_peak")


def test_finance_outputs_npv_payback_and_annual_table(tmp_path: Path) -> None:
    paths = generate_sample_csvs(tmp_path)
    case = load_case_config("src/investment_estimation/configs/mvp_demo.yaml")
    result = run_case_from_yaml("src/investment_estimation/configs/mvp_demo.yaml")

    npv = compute_npv(result["monthly"], case.project, discount_rate=0.08)
    payback = compute_payback_years(result["monthly"], case.project)
    table = annual_cashflow_table(result["monthly"], case.project, discount_rate=0.08)

    assert paths["load"].exists()
    assert npv > 0
    assert payback is not None
    assert {"year", "cashflow", "discounted_cashflow", "cumulative_cashflow"}.issubset(table.columns)


def test_capacity_search_outputs_candidates_and_reasons(tmp_path: Path) -> None:
    result = run_search_from_yaml("src/investment_estimation/configs/v1_capacity_search_demo.yaml")

    candidates = result["candidates"]
    infeasible = result["infeasible"]
    best = result["best"]

    assert not candidates.empty
    assert "is_feasible" in candidates.columns
    assert "infeasible_reasons" in candidates.columns
    assert best is None or bool(best["is_feasible"])
    assert isinstance(infeasible, pd.DataFrame)


def test_v2_yaml_loads_objective_mode() -> None:
    case = load_case_config("src/investment_estimation/configs/v2_owner_saving_first_demo.yaml")

    assert case.search.objective_mode == "owner_saving_first"


def test_capacity_search_objective_mode_changes_best_candidate(tmp_path: Path) -> None:
    timeseries = _ranking_demo_timeseries()
    investor_case = _ranking_demo_case(tmp_path, "investor_irr_first")
    owner_case = replace(investor_case, search=replace(investor_case.search, objective_mode="owner_saving_first"))

    investor_result = run_capacity_search(timeseries, investor_case)
    owner_result = run_capacity_search(timeseries, owner_case)

    assert investor_result["best"]["ppa_price"] == 0.6
    assert investor_result["best"]["objective_mode"] == "investor_irr_first"
    assert investor_result["best"]["ranking_primary_metric"] == "project_irr"
    assert owner_result["best"]["ppa_price"] == 0.3
    assert owner_result["best"]["objective_mode"] == "owner_saving_first"
    assert owner_result["best"]["ranking_primary_metric"] == "owner_saving_pct"


def test_v5_investor_irr_uplift_outputs_baseline_metrics(tmp_path: Path) -> None:
    timeseries = _ranking_demo_timeseries()
    case = _ranking_demo_case(tmp_path, "investor_irr_uplift")

    result = run_capacity_search(timeseries, case)
    best = result["best"]

    assert best["objective_mode"] == "investor_irr_uplift"
    assert best["ranking_primary_metric"] == "irr_uplift"
    assert best["baseline_project_irr"] is not None
    assert best["candidate_project_irr"] == best["project_irr"]
    assert best["irr_uplift"] == pytest.approx(best["candidate_project_irr"] - best["baseline_project_irr"])


def _ranking_demo_timeseries() -> pd.DataFrame:
    time = pd.date_range("2026-01-01", periods=24, freq="1h")
    return pd.DataFrame(
        {
            "time": time,
            "load_kw": [10.0] * len(time),
            "price": [1.0] * len(time),
            "price_type": ["flat"] * len(time),
            "pv_kw": [0.0] * len(time),
            "wind_kw": [10.0] * len(time),
            "dt_hours": [1.0] * len(time),
        }
    )


def _ranking_demo_case(tmp_path: Path, objective_mode: str) -> CaseConfig:
    finance = FinanceConfig(
        project_years=5,
        capex_wind_per_kw=5.0,
        capex_pv_per_kw=0.0,
        capex_bess_power_per_kw=0.0,
        capex_bess_energy_per_kwh=0.0,
        fixed_om_pct_of_capex=0.0,
        renewable_degradation_pct=0.0,
        bess_replacement_year=None,
    )
    project = ProjectConfig(
        wind_capacity_kw=100.0,
        pv_capacity_kw=0.0,
        ppa_price=0.5,
        export_price=0.0,
        bess=BESSConfig(power_kw=0.0, energy_kwh=0.0),
        finance=finance,
    )
    search = CapacitySearchConfig(
        enabled=True,
        wind_capacity_kw=(100.0,),
        pv_capacity_kw=(0.0,),
        bess_power_kw=(0.0,),
        bess_energy_kwh=(0.0,),
        ppa_price=(0.3, 0.6),
        min_project_irr=-0.5,
        min_owner_saving_pct=0.0,
        objective_mode=objective_mode,
    )
    paths = PathConfig(
        load_csv=tmp_path / "load.csv",
        price_csv=tmp_path / "price.csv",
        resource_csv=tmp_path / "resource.csv",
        monthly_output_csv=tmp_path / "monthly.csv",
        dispatch_output_csv=tmp_path / "dispatch.csv",
    )
    baseline_project = None
    if objective_mode == "investor_irr_uplift":
        baseline_project = BaselineProjectConfig(
            wind_capacity_kw=100.0,
            pv_capacity_kw=0.0,
            ppa_price=0.5,
            bess_power_kw=0.0,
            bess_energy_kwh=0.0,
        )
    return CaseConfig(
        name=f"ranking_{objective_mode}",
        paths=paths,
        project=project,
        search=search,
        baseline_project=baseline_project,
    )
