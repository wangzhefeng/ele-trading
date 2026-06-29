import csv
from pathlib import Path

import pandas as pd
import pytest

from app.capacity_planning.run_wind_pv_bess_irr_planning import _write_result_csv_with_cn_header
from ele_trading.capacity_planning.wind_pv_bess_irr_planner import WindPVBESSIRRResult
from ele_trading.capacity_planning.wind_pv_bess_irr_tuning import _result_summary_row


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
                "curtail_kwh": 35_922_931.457950,
            }
        },
    )

    row = _result_summary_row(result, {"target_irr": 0.05}, stage="coarse")

    assert row["annual_green_generation_kwh"] == pytest.approx(547_868_235.664246)
    assert row["annual_green_used_kwh"] == 511_945_304.206296
    assert row["annual_grid_buy_kwh"] == 654_198_933.297295


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
