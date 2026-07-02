from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from invest_est_models.config_loader import load_case_config
from invest_est_models.app.run_capacity_search import run_search_from_yaml
from invest_est_models.app.run_mvp_demo import run_case_from_yaml
from invest_est_models.data_provider import (
    build_timeseries,
    generate_sample_csvs,
    read_load_csv,
    read_price_csv,
    read_resource_csv,
    validate_timeseries,
)
from invest_est_models.finance import annual_cashflow_table, compute_npv, compute_payback_years


def test_v1_yaml_loads_search_and_settlement_config() -> None:
    case = load_case_config("src/invest_est_models/configs/v1_capacity_search_demo.yaml")

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


def test_finance_outputs_npv_payback_and_annual_table(tmp_path: Path) -> None:
    paths = generate_sample_csvs(tmp_path)
    case = load_case_config("src/invest_est_models/configs/mvp_demo.yaml")
    result = run_case_from_yaml("src/invest_est_models/configs/mvp_demo.yaml")

    npv = compute_npv(result["monthly"], case.project, discount_rate=0.08)
    payback = compute_payback_years(result["monthly"], case.project)
    table = annual_cashflow_table(result["monthly"], case.project, discount_rate=0.08)

    assert paths["load"].exists()
    assert npv > 0
    assert payback is not None
    assert {"year", "cashflow", "discounted_cashflow", "cumulative_cashflow"}.issubset(table.columns)


def test_capacity_search_outputs_candidates_and_reasons(tmp_path: Path) -> None:
    result = run_search_from_yaml("src/invest_est_models/configs/v1_capacity_search_demo.yaml")

    candidates = result["candidates"]
    infeasible = result["infeasible"]
    best = result["best"]

    assert not candidates.empty
    assert "is_feasible" in candidates.columns
    assert "infeasible_reasons" in candidates.columns
    assert best is None or bool(best["is_feasible"])
    assert isinstance(infeasible, pd.DataFrame)
