from __future__ import annotations

import pandas as pd

from ba_eva.eva_PV_optim_version import storage_optim_Wind_BESS_1, storage_optim_Wind_BESS_2
from ba_eva.eva_PV_optim_version.storage_optim_Wind_BESS import plan_energy_system
from ba_eva.eva_PV_optim_version.storage_optim_common import PlanConfigFast, UnitsConfig


def test_plan_energy_system_returns_feasible_solution_for_simple_wind_case():
    time_index = pd.date_range("2025-01-01 00:00:00", periods=4, freq="1h")
    df_load = pd.DataFrame({"P_kw": [5.0, 5.0, 5.0, 5.0]}, index=time_index)
    df_wind = pd.DataFrame({"WindPower_MW": [0.005, 0.005, 0.005, 0.005]}, index=time_index)

    result = plan_energy_system(
        df_load=df_load,
        wind_input=df_wind,
        cfg=PlanConfigFast(
            self_use_ratio_min=0.9,
            load_cover_ratio_min=0.9,
            batt_hi_max_kwh=10.0,
        ),
        units=UnitsConfig(load_power="kW", wind_power="MW"),
    )

    assert result["feasible"] is True
    assert "solution" in result
    assert result["solution"]["metrics"]["self_use_ratio"] >= 0.9
    assert result["solution"]["metrics"]["load_cover_ratio"] >= 0.9


def test_plan_energy_system_reports_load_stage_error_for_invalid_load_column():
    time_index = pd.date_range("2025-01-01 00:00:00", periods=2, freq="1h")
    df_load = pd.DataFrame({"bad_col": [1.0, 2.0]}, index=time_index)

    result = plan_energy_system(df_load=df_load)

    assert result["feasible"] is False
    assert result["diagnosis"]["stage"] == "load"


def test_plan_energy_system_reports_wind_stage_error_for_invalid_wind_column():
    time_index = pd.date_range("2025-01-01 00:00:00", periods=2, freq="1h")
    df_load = pd.DataFrame({"P_kw": [1.0, 2.0]}, index=time_index)
    df_wind = pd.DataFrame({"bad_wind": [1.0, 2.0]}, index=time_index)

    result = plan_energy_system(df_load=df_load, wind_input=df_wind)

    assert result["feasible"] is False
    assert result["diagnosis"]["stage"] == "wind"


def test_plan_energy_system_reports_pv_stage_error_for_invalid_pv_column():
    time_index = pd.date_range("2025-01-01 00:00:00", periods=2, freq="1h")
    df_load = pd.DataFrame({"P_kw": [1.0, 2.0]}, index=time_index)
    df_pv = pd.DataFrame({"bad_pv": [1.0, 2.0]}, index=time_index)

    result = plan_energy_system(df_load=df_load, pv_unit_kw=df_pv)

    assert result["feasible"] is False
    assert result["diagnosis"]["stage"] == "pv"


def test_plan_energy_system_reports_no_generation_when_inputs_are_missing():
    time_index = pd.date_range("2025-01-01 00:00:00", periods=2, freq="1h")
    df_load = pd.DataFrame({"P_kw": [1.0, 2.0]}, index=time_index)

    result = plan_energy_system(df_load=df_load)

    assert result["feasible"] is False
    assert result["diagnosis"]["reason"] == "NO_GENERATION"
    assert "msg" in result["diagnosis"]


def test_legacy_scripts_use_the_unified_plan_entrypoint():
    assert storage_optim_Wind_BESS_1.plan_energy_system is plan_energy_system
    assert storage_optim_Wind_BESS_2.plan_energy_system is plan_energy_system
