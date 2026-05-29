from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ele_trading.forecasting.base import ForecastOutput


def test_build_daily_energy_targets_expands_monthly_values():
    from ele_trading.data_provider.load_profile import build_daily_energy_targets

    daily = build_daily_energy_targets(
        target_year=2025,
        monthly_energy_targets={1: 310.0, 2: 280.0},
    )

    assert daily[pd.Timestamp("2025-01-01").date()] == pytest.approx(10.0)
    assert daily[pd.Timestamp("2025-02-01").date()] == pytest.approx(10.0)
    assert len(daily) == 59


def test_fill_missing_load_by_daily_energy_returns_quality_flags():
    from ele_trading.data_provider.load_profile import fill_missing_load_by_daily_energy

    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2025-01-01 00:00:00",
                    "2025-01-01 00:15:00",
                    "2025-01-01 00:30:00",
                    "2025-01-01 00:45:00",
                ]
            ),
            "load_kw": [10.0, None, 30.0, None],
            "is_original": [True, False, True, False],
            "is_interpolated": [False, False, False, False],
            "is_shifted_from_history": [False, False, False, False],
            "is_filled_by_nearest_day": [False, False, False, False],
            "source_file": ["raw.xlsx"] * 4,
        }
    )

    result = fill_missing_load_by_daily_energy(
        df=df,
        target_year=2025,
        daily_energy_targets={pd.Timestamp("2025-01-01").date(): 30.0},
        freq="15min",
    )

    assert set(
        [
            "timestamp",
            "load_kw",
            "is_original",
            "is_interpolated",
            "is_shifted_from_history",
            "is_filled_by_nearest_day",
            "source_file",
            "quality_score",
        ]
    ).issubset(result.columns)
    assert result["load_kw"].notna().all()
    assert result["is_interpolated"].sum() == 2
    assert (result["quality_score"] <= 1.0).all()


def test_build_load_profile_from_raw_covers_target_year(tmp_path: Path):
    from ele_trading.data_provider.load_profile import (
        LoadProfileBuildConfig,
        build_load_profile_from_raw,
    )

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    source = pd.DataFrame(
        {
            "数据日期": ["2024-09-01"] * 4,
            "时间": ["00:00:00", "00:15:00", "00:30:00", "00:45:00"],
            "功率(KW)": [10.0, 20.0, 30.0, 40.0],
        }
    )
    source.to_excel(raw_dir / "sample.xlsx", index=False)

    config = LoadProfileBuildConfig(
        target_year=2025,
        freq="15min",
        date_col="数据日期",
        time_col="时间",
        power_col="功率(KW)",
        monthly_energy_targets=None,
        history_source_year=2024,
        history_source_month_start=9,
        smoothing_window=3,
        fill_missing_points=False,
        fill_missing_days=True,
    )

    result = build_load_profile_from_raw(raw_dir, config)

    assert result.data["timestamp"].dt.year.nunique() == 1
    assert result.data["timestamp"].dt.year.iloc[0] == 2025
    assert "filled_days_count" in result.summary


def test_load_or_build_pv_profile_clear_sky_returns_metadata():
    from ele_trading.resource_simulation import (
        PVProfileConfig,
        load_or_build_pv_profile,
    )

    index = pd.date_range("2025-06-01 00:00:00", periods=96, freq="15min")
    config = PVProfileConfig(
        latitude=28.42,
        longitude=117.88,
        timezone="Asia/Shanghai",
        capacity_kwp=100.0,
        tilt=None,
        azimuth=180.0,
        system_loss=0.2,
        temp_coeff=-0.004,
        cloud_factor=0.75,
        mode="clear_sky",
    )

    result = load_or_build_pv_profile(config=config, time_index=index)

    assert len(result.power_series) == len(index)
    assert result.power_series.min() >= 0.0
    assert result.metadata["mode"] == "clear_sky"
    assert "equivalent_hours" in result.metadata


def test_load_or_build_wind_profile_from_local_weather_respects_cap():
    from ele_trading.resource_simulation import (
        WindProfileConfig,
        load_or_build_wind_profile,
    )

    index = pd.date_range("2025-01-01 00:00:00", periods=48, freq="h")
    weather_df = pd.DataFrame(
        {
            "wind_speed_100m": [6.0] * len(index),
            "temperature_2m": [15.0] * len(index),
        },
        index=index,
    )
    config = WindProfileConfig(
        year=2025,
        freq="1h",
        farm_capacity_mw=20.0,
        target_full_load_hours=1800.0,
        mean_wind_speed_target=6.0,
        meteo_height_m=100.0,
        met_mast_height_m=140.0,
        hub_height_m=140.0,
        shear_alpha=0.2,
        rated_power_kw=5000.0,
        cut_in=3.0,
        rated_speed=11.0,
        cut_out=25.0,
        max_power_ratio=1.2,
        mode="resource_simulation",
    )

    result = load_or_build_wind_profile(config=config, weather_df=weather_df)

    assert len(result.power_series) == len(weather_df)
    assert result.power_series.max() <= 24.0 + 1e-6
    assert result.metadata["mode"] == "resource_simulation"


def test_build_case_datasets_use_different_output_contracts():
    from ele_trading.data_provider.case_dataset import (
        build_investment_case_dataset,
        build_trading_case_dataset,
    )

    index = pd.date_range("2025-01-01 00:00:00", periods=3, freq="h")
    load_df = pd.DataFrame(
        {
            "timestamp": index,
            "load_kw": [100.0, 120.0, 90.0],
            "quality_score": [1.0, 0.9, 1.0],
        }
    )
    pv_series = pd.Series([10.0, 20.0, 0.0], index=index, name="pv_kw")
    wind_series = pd.Series([5.0, 5.0, 5.0], index=index, name="wind_kw")
    prices = pd.DataFrame(
        {
            "timestamp": index,
            "buy_price": [0.5, 0.6, 0.4],
            "sell_price": [0.2, 0.2, 0.2],
        }
    )

    investment = build_investment_case_dataset(load_df, pv_series, wind_series, prices)
    trading = build_trading_case_dataset(load_df, pv_series, wind_series, prices)

    assert "net_load_kw" in investment.frame.columns
    assert "price_forecast" in trading.frame.columns
    assert "scenario_id" in trading.frame.columns


def test_renewable_forecaster_supports_precomputed_profile():
    from ele_trading.forecasting.renewable_forecast import RenewableForecaster

    fc = RenewableForecaster()
    result = fc.predict_from_profile([1.0, 2.0, 3.0], horizon=2)

    assert isinstance(result, ForecastOutput)
    assert result.horizon == 2
    assert result.point_forecast == [1.0, 2.0]
