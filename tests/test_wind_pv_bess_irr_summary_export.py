import csv
from pathlib import Path

import pandas as pd
import pytest

from app.capacity_planning.run_wind_pv_bess_irr_planning import (
    _build_optimal_solution_df,
    _write_result_csv_with_cn_header,
)
from investment_estimation.todo.wind_pv_bess_irr_planner import WindPVBESSIRRResult
from investment_estimation.todo.wind_pv_bess_irr_tuning import (
    _result_summary_row,
    run_wind_pv_bess_irr_resource_tuning,
)


def test_result_summary_row_includes_annual_energy_metrics_for_diagnostic_best():
    result = WindPVBESSIRRResult(
        status="no_solution",
        diagnostic_summary={
            "max_irr_candidate": {
                "reason": "irr_out_of_tolerance",
                "wind_mw": 110.0,
                "pv_mw": 140.0,
                "bess_mwh": 0.0,
                "annual_green_used_kwh": 511_945_304.206296,
                "annual_grid_buy_kwh": 654_198_933.297295,
                "annual_green_generation_kwh": 560_000_000.0,
                "curtail_kwh": 35_922_931.457950,
            }
        },
    )

    row = _result_summary_row(result, {"target_irr": 0.05}, stage="coarse")

    # 发电量取自调度口径字段，而非 used+curtail 重构（560M ≠ used+curtail≈547.9M）
    assert row["annual_green_generation_kwh"] == pytest.approx(560_000_000.0)
    assert row["annual_green_used_kwh"] == 511_945_304.206296
    assert row["annual_grid_buy_kwh"] == 654_198_933.297295


def test_result_summary_row_includes_project_finance_irr_fields():
    result = WindPVBESSIRRResult(
        status="ok",
        irr=0.05,
        irr_ti_pre=0.06,
        irr_ti_post=0.055,
        irr_eq_pre=0.052,
        irr_eq_post=0.05,
    )

    row = _result_summary_row(result, {"target_irr": 0.05}, stage="fine")

    assert row["irr"] == pytest.approx(0.05)
    assert row["irr_ti_pre"] == pytest.approx(0.06)
    assert row["irr_ti_post"] == pytest.approx(0.055)
    assert row["irr_eq_pre"] == pytest.approx(0.052)
    assert row["irr_eq_post"] == pytest.approx(0.05)


def test_write_result_csv_with_cn_header_places_chinese_labels_above_english(tmp_path: Path):
    output_path = tmp_path / "parameter_search_summary.csv"
    df = pd.DataFrame([
        {
            "scenario_id": 26,
            "annual_green_generation_kwh": 547_868_235.664246,
            "annual_green_used_kwh": 511_945_304.206296,
            "annual_grid_buy_kwh": 654_198_933.297295,
        }
    ])

    _write_result_csv_with_cn_header(df, output_path)

    with output_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    assert rows[0] == ["场景编号", "年度绿电发电量(kWh)", "年度绿电消纳量(kWh)", "年度电网购电量(kWh)"]
    assert rows[1] == ["scenario_id", "annual_green_generation_kwh", "annual_green_used_kwh", "annual_grid_buy_kwh"]
    assert rows[2][0] == "26"


def test_write_result_csv_with_cn_header_labels_project_finance_irr_fields(tmp_path: Path):
    output_path = tmp_path / "optimal_solution.csv"
    df = pd.DataFrame([
        {
            "annual_cashflow_yuan": 100.0,
            "irr": 0.05,
            "irr_ti_pre": 0.06,
            "irr_ti_post": 0.055,
            "irr_eq_pre": 0.052,
            "irr_eq_post": 0.05,
        }
    ])

    _write_result_csv_with_cn_header(df, output_path)

    with output_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    assert rows[0] == [
        "资本金税后平均年度现金流(元)",
        "资本金IRR税后",
        "全投资IRR税前",
        "全投资IRR税后",
        "资本金IRR税前",
        "资本金IRR税后",
    ]


def test_write_result_csv_with_cn_header_labels_runtime_diagnostics(tmp_path: Path):
    output_path = tmp_path / "parameter_search_summary.csv"
    df = pd.DataFrame([
        {
            "stage_index": 1,
            "stage_total": 2,
            "elapsed_seconds": 1.25,
            "total_combinations": 12,
            "reason_counts": {"ok": 1},
            "worker_pid": 12345,
        }
    ])

    _write_result_csv_with_cn_header(df, output_path)

    with output_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    assert rows[0] == [
        "阶段内序号",
        "阶段场景总数",
        "场景耗时(秒)",
        "候选组合数",
        "候选原因分布",
        "进程ID",
    ]


def test_build_optimal_solution_df_ranks_lowest_capex_candidate_as_best():
    """diagnostics 按容量遍历序给出，rank=1 必须落在最低投资的最优解上。"""
    diagnostics = pd.DataFrame([
        {"reason": "ok", "wind_mw": 0.0, "pv_mw": 10.0, "bess_mwh": 100.0,
         "total_capex_yuan": 115_000.0, "irr": 0.051, "irr_gap": 0.001},
        {"reason": "ok", "wind_mw": 10.0, "pv_mw": 0.0, "bess_mwh": 0.0,
         "total_capex_yuan": 50_000.0, "irr": 0.060, "irr_gap": 0.010},
    ])
    result = WindPVBESSIRRResult(status="ok", diagnostics=diagnostics)

    df = _build_optimal_solution_df(result)

    assert bool(df.iloc[0]["is_best_solution"]) is True
    assert float(df.iloc[0]["total_capex_yuan"]) == 50_000.0
    assert float(df.iloc[0]["wind_mw"]) == 10.0
    assert bool(df.iloc[1]["is_best_solution"]) is False


def _tiny_tuning_config(parallel_enabled: bool) -> dict:
    return {
        "wind_simulation": {"target_full_load_hours": 1000.0},
        "pv_simulation": {"cloud_factor": 0.8, "system_loss": 0.1},
        "search": {"wind_step_mw": 1.0, "pv_step_mw": 1.0, "bess_step_mwh": 1.0},
        "capacity": {"pv_max_mw": 0.0},
        "resource_tuning": {
            "parallel_enabled": parallel_enabled,
            "max_workers": 2,
            "incremental_write": True,
            "retain_intermediate_diagnostics": False,
            "wind_target_full_load_hours_min": 1000.0,
            "wind_target_full_load_hours_max": 1000.0,
            "wind_target_full_load_hours_step": 100.0,
            "pv_cloud_factor_min": 0.8,
            "pv_cloud_factor_max": 0.83,
            "pv_cloud_factor_step": 0.03,
            "pv_system_loss_min": 0.1,
            "pv_system_loss_max": 0.1,
            "pv_system_loss_step": 0.1,
            "fine_scenario_limit": 1,
            "coarse_search_bounds": {
                "wind_min_mw": 0.0,
                "wind_max_mw": 1.0,
                "wind_step_mw": 1.0,
                "pv_min_mw": 0.0,
                "pv_max_mw": 0.0,
                "pv_step_mw": 1.0,
                "bess_min_mwh": 0.0,
                "bess_max_mwh": 0.0,
                "bess_step_mwh": 1.0,
            },
            "fine_search_window": {"wind_mw": 0.0, "pv_mw": 0.0, "bess_mwh": 0.0},
            "fine_search_step": {"wind_mw": 1.0, "pv_mw": 1.0, "bess_mwh": 1.0},
        },
    }


def _tiny_curves():
    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    df_load = pd.DataFrame({"Time": idx, "P_kw": 1000.0})
    wind_unit = pd.Series(1000.0, index=idx, name="wind_unit_kw")
    pv_unit = pd.Series(1.0, index=idx, name="pv_unit_kw")
    return df_load, idx, wind_unit, pv_unit


def test_resource_tuning_serial_and_parallel_keep_stable_summary_order(tmp_path: Path):
    from investment_estimation.todo.wind_pv_bess_irr_planner import WindPVBESSIRRPlanConfig

    df_load, idx, wind_unit, pv_unit = _tiny_curves()
    cfg = WindPVBESSIRRPlanConfig(
        wind_max_mw=1.0,
        pv_max_mw=0.0,
        bess_max_mwh=0.0,
        wind_step_mw=1.0,
        pv_step_mw=1.0,
        bess_step_mwh=1.0,
        target_irr=0.0,
        irr_tolerance=10.0,
        wind_capex_yuan_per_kw=50.0,
        annual_opex_ratio=0.0,
    )

    def build_wind(_config, _path):
        return wind_unit

    def build_pv(_config, _time_index, _path):
        return pv_unit

    def curve_path(_data_dir, prefix, _section):
        return _data_dir / f"{prefix}.csv"

    serial_updates: list[pd.DataFrame] = []
    serial = run_wind_pv_bess_irr_resource_tuning(
        _tiny_tuning_config(parallel_enabled=False),
        df_load,
        idx,
        tmp_path,
        cfg,
        build_wind_unit_curve=build_wind,
        build_pv_unit_curve=build_pv,
        curve_cache_path=curve_path,
        on_summary_update=serial_updates.append,
    )
    parallel_updates: list[pd.DataFrame] = []
    parallel = run_wind_pv_bess_irr_resource_tuning(
        _tiny_tuning_config(parallel_enabled=True),
        df_load,
        idx,
        tmp_path,
        cfg,
        build_wind_unit_curve=build_wind,
        build_pv_unit_curve=build_pv,
        curve_cache_path=curve_path,
        on_summary_update=parallel_updates.append,
    )

    assert serial.result is not None
    assert parallel.result is not None
    assert serial.best_summary["scenario_id"] == parallel.best_summary["scenario_id"]
    assert list(parallel.parameter_search_summary["stage"]) == ["coarse", "coarse", "fine"]
    assert list(parallel.parameter_search_summary["stage_index"]) == [1, 2, 1]
    for column in ("elapsed_seconds", "total_combinations", "reason_counts", "worker_pid"):
        assert column in parallel.parameter_search_summary.columns
    assert serial_updates
    assert parallel_updates
