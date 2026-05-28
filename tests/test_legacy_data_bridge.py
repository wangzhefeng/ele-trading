from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_prepare_legacy_temp_data_uses_cached_outputs(tmp_path: Path):
    import sys
    from pathlib import Path as _Path
    _app_dir = str(_Path(__file__).resolve().parents[1] / "app")
    if _app_dir not in sys.path:
        sys.path.insert(0, _app_dir)
    from run_legacy_data_preparation import (
        build_legacy_temp_data,
    )

    data_dir = tmp_path / "temp"
    data_dir.mkdir()

    load_path = data_dir / "df_2025.csv"
    pv_path = data_dir / "df_pv_2025.csv"
    wind_path = data_dir / "df_wind_2025.csv"
    total_path = data_dir / "df_total.csv"

    timestamps = pd.date_range("2025-01-01 00:00:00", periods=4, freq="15min")
    pd.DataFrame({"Time": timestamps, "P_kw": [100.0, 110.0, 120.0, 130.0]}).to_csv(load_path, index=False)
    pd.DataFrame({"Time": timestamps, "pv_kw": [10.0, 20.0, 30.0, 40.0]}).to_csv(pv_path, index=False)
    pd.DataFrame({"Time": timestamps, "WindPower_MW": [0.01, 0.02, 0.03, 0.04]}).to_csv(wind_path, index=False)

    config = {
        "run": {"target_year": 2025, "freq": "15min", "refresh_mode": "use_cached", "write_df_total": True},
        "paths": {
            "raw_load_dir": str(PROJECT_ROOT / "data" / "wind_pv_es_calc" / "负荷曲线"),
            "df_2025_path": str(load_path),
            "df_pv_2025_path": str(pv_path),
            "df_wind_2025_path": str(wind_path),
            "df_total_path": str(total_path),
        },
        "load_profile": {
            "target_year": 2025,
            "freq": "15min",
            "date_col": "数据日期",
            "time_col": "时间",
            "power_col": "功率(KW)",
            "monthly_energy_targets": None,
            "history_source_year": 2024,
            "history_source_month_start": 9,
            "smoothing_window": 3,
            "fill_missing_points": False,
            "fill_missing_days": False,
        },
        "pv_profile": {
            "latitude": 28.42,
            "longitude": 117.88,
            "timezone": "Asia/Shanghai",
            "capacity_kwp": 100.0,
            "tilt": None,
            "azimuth": 180.0,
            "system_loss": 0.2,
            "temp_coeff": -0.004,
            "cloud_factor": 0.75,
            "mode": "clear_sky",
        },
        "wind_profile": {
            "year": 2025,
            "freq": "1h",
            "farm_capacity_mw": 20.0,
            "target_full_load_hours": 1800.0,
            "mean_wind_speed_target": 6.0,
            "meteo_height_m": 100.0,
            "met_mast_height_m": 140.0,
            "hub_height_m": 140.0,
            "shear_alpha": 0.2,
            "rated_power_kw": 5000.0,
            "cut_in": 3.0,
            "rated_speed": 11.0,
            "cut_out": 25.0,
            "max_power_ratio": 1.2,
            "mode": "resource_simulation",
        },
        "compatibility": {"time_col": "Time", "load_col": "P_kw", "pv_col": "pv_kw", "wind_col": "WindPower_MW"},
    }

    result = build_legacy_temp_data(config)

    assert set(["load", "pv", "wind"]).issubset(result)
    assert list(result["load"].columns) == ["Time", "P_kw"]
    assert "pv_kw" in result["pv"].columns
    assert list(result["wind"].columns) == ["Time", "WindPower_MW"]
    assert total_path.exists()

    total_df = pd.read_csv(total_path)
    assert "NetLoad_kw" in total_df.columns
    assert total_df["NetLoad_kw"].iloc[0] == 100.0 - 10.0 - 10.0


def test_bridge_config_file_exists_and_defaults_to_cached():
    config_path = PROJECT_ROOT / "configs" / "wind_pv_es_calc_data_bridge.yaml"
    assert config_path.exists()

    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    assert config["run"]["refresh_mode"] == "use_cached"
    assert config["run"]["write_df_total"] is True

