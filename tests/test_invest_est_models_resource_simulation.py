from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from invest_est_models.app.build_resource_profile import build_resource_profile_from_paths
from invest_est_models.resource_simulation import PVProfileConfig, SimulationResult, load_or_build_pv_profile
from invest_est_models.resource_simulation.weather import fetch_weather_open_meteo
from invest_est_models.resource_simulation.wind_simulation_v1 import (
    WindProfileConfig,
    rescale_wind_output_to_target_flh,
)


def test_simulation_result_keeps_common_fields() -> None:
    series = pd.Series([0.0, 10.0], index=pd.date_range("2026-01-01", periods=2, freq="1h"), name="pv_kw")

    result = SimulationResult(power_series=series, total_generation_mwh=0.01, scale_factor=1.0)

    assert result.power_series.name == "pv_kw"
    assert result.total_generation_mwh == pytest.approx(0.01)
    assert result.scale_factor == pytest.approx(1.0)
    assert result.selected_turbine is None


def test_pv_replay_mode_outputs_pv_kw() -> None:
    weather = pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=3, freq="1h"),
            "pv_kw": [0.0, 12.0, 0.0],
        }
    ).set_index("time")
    config = PVProfileConfig(
        latitude=30.0,
        longitude=120.0,
        timezone="Asia/Shanghai",
        capacity_kwp=20.0,
        tilt=None,
        azimuth=180.0,
        system_loss=0.15,
        temp_coeff=-0.004,
        cloud_factor=None,
        mode="replay",
    )

    result = load_or_build_pv_profile(config=config, weather_df=weather)

    assert result.power_series.name == "pv_kw"
    assert result.power_series.tolist() == [0.0, 12.0, 0.0]
    assert result.total_generation_mwh == pytest.approx(0.012)


def test_wind_full_load_hour_rescale_preserves_peak_limit() -> None:
    config = WindProfileConfig(
        year=2026,
        freq="1h",
        farm_capacity_mw=10.0,
        mean_wind_speed_target=None,
        target_full_load_hours=1200.0,
        meteo_height_m=100.0,
        met_mast_height_m=140.0,
        hub_height_m=140.0,
        shear_alpha=0.14,
        rated_power_kw=5000.0,
        cut_in=3.0,
        rated_speed=11.0,
        cut_out=25.0,
        max_power_ratio=1.2,
        mode="resource_simulation",
    )
    raw_power_mw = np.array([0.0, 2.0, 6.0, 20.0] * 300, dtype=float)

    scaled = rescale_wind_output_to_target_flh(raw_power_mw, dt_hours=1.0, config=config)
    full_load_hours = scaled.sum() / config.farm_capacity_mw

    assert scaled.max() <= config.farm_capacity_mw * config.max_power_ratio
    assert full_load_hours == pytest.approx(config.target_full_load_hours, abs=1.0)


def test_build_resource_profile_outputs_required_columns(tmp_path: Path) -> None:
    pv_path = tmp_path / "pv.csv"
    wind_path = tmp_path / "wind.csv"
    output_path = tmp_path / "resource.csv"
    time = pd.date_range("2026-01-01", periods=2, freq="1h")
    pd.DataFrame({"time": time, "pv_kw": [1.0, 2.0]}).to_csv(pv_path, index=False)
    pd.DataFrame({"time": time, "wind_kw": [3.0, 4.0]}).to_csv(wind_path, index=False)

    result = build_resource_profile_from_paths(pv_path=pv_path, wind_path=wind_path, output_path=output_path)

    assert output_path.exists()
    assert result.columns.tolist() == ["time", "pv_kw", "wind_kw"]
    assert result[["pv_kw", "wind_kw"]].sum().to_dict() == {"pv_kw": 3.0, "wind_kw": 7.0}


def test_fetch_weather_open_meteo_uses_requests_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "hourly": {
                    "time": ["2026-01-01T00:00", "2026-01-01T01:00"],
                    "wind_speed_100m": [5.0, 6.0],
                    "temperature_2m": [10.0, 11.0],
                }
            }

    calls: list[dict[str, object]] = []

    def fake_get(url: str, params: dict[str, object], timeout: int) -> FakeResponse:
        calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("invest_est_models.resource_simulation.weather.requests.get", fake_get)

    result = fetch_weather_open_meteo(
        latitude=30.0,
        longitude=120.0,
        start_date="2026-01-01",
        end_date="2026-01-01",
    )

    assert calls
    assert result.columns.tolist() == ["timestamp", "wind_speed_100m", "temperature_2m"]
    assert result["wind_speed_100m"].tolist() == [5.0, 6.0]
