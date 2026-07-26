from __future__ import annotations

import pandas as pd

from ele_trading.forecasting.base import ForecastOutput


def test_load_or_build_pv_profile_clear_sky_returns_metadata():
    from investment_estimation.todo.resource_simulation import (
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
    from investment_estimation.todo.resource_simulation import (
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
    assert result.power_series.max() <= 24000.0 + 1e-6  # kW (20 MW × 1.2)
    assert result.metadata["mode"] == "resource_simulation"


def test_build_trading_dataset_returns_market_snapshot():
    from ele_trading.data_provider.market_data import build_trading_case_dataset

    index = pd.date_range(
        "2025-01-01 00:00:00",
        periods=3,
        freq="h",
        tz="Asia/Shanghai",
    )
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

    trading = build_trading_case_dataset(
        load_df,
        pv_series,
        wind_series,
        prices,
        market="mengxi",
        scope_type="portfolio",
        scope_id="fixture",
        as_of=index[-1],
        version="fixture-v1",
    )

    assert "price_forecast" in trading.frame.columns
    assert "scenario_id" in trading.frame.columns
    assert trading.version == "fixture-v1"


def test_renewable_forecaster_supports_precomputed_profile():
    from ele_trading.forecasting.renewable_forecast import RenewableForecaster

    fc = RenewableForecaster()
    result = fc.predict_from_profile([1.0, 2.0, 3.0], horizon=2)

    assert isinstance(result, ForecastOutput)
    assert result.horizon == 2
    assert result.point_forecast == [1.0, 2.0]
