"""Round-1/2 review regressions for Phase 3 forecasting."""

from __future__ import annotations

import pandas as pd
import pytest


ISSUE_TIME = pd.Timestamp("2026-07-01 10:00", tz="Asia/Shanghai")


def _request(target: str, **overrides):
    from ele_trading.forecasting.contracts import ForecastRequest

    values = {
        "target": target,
        "scope_type": "site",
        "scope_id": "north-1",
        "horizon": 2,
        "frequency": "15min",
        "issue_time": ISSUE_TIME,
        "quantiles": (0.1, 0.9),
    }
    values.update(overrides)
    return ForecastRequest(**values)


def _valid_times(request) -> pd.DatetimeIndex:
    from ele_trading.forecasting.contracts import _valid_time_index

    return _valid_time_index(request)


def _weather_result(
    renewable_request,
    *,
    weather_variable: str,
    unit: str,
    scope_type: str | None = None,
    scope_id: str | None = None,
):
    from ele_trading.forecasting.contracts import ForecastResult

    weather_request = _request(
        "weather",
        scope_type=scope_type or renewable_request.scope_type,
        scope_id=scope_id or renewable_request.scope_id,
        horizon=renewable_request.horizon,
        frequency=renewable_request.frequency,
        issue_time=renewable_request.issue_time,
        quantiles=renewable_request.quantiles,
        data={"weather_variable": weather_variable},
    )
    point = pd.Series(
        [12.0] * renewable_request.horizon,
        index=_valid_times(weather_request),
    )
    return ForecastResult(
        request=weather_request,
        point=point,
        quantiles={
            level: point.copy()
            for level in renewable_request.quantiles
        },
        unit=unit,
        model_version="weather-v1",
        feature_as_of=renewable_request.issue_time
        - pd.Timedelta(minutes=15),
    )


def test_physical_renewable_rejects_bare_weather_array_without_as_of():
    """Inventing request.issue_time for an unversioned weather array must fail."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    model = RenewablePowerForecastModel(
        target="wind_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 5.0},
    )

    with pytest.raises(ValueError, match="feature_as_of|weather forecast"):
        model.forecast(
            _request(
                "wind_power",
                data={
                    "wind_speed": [8.0, 9.0],
                    "weather_variable": "wind_speed",
                    "weather_unit": "m/s",
                },
            )
        )


def test_physical_renewable_uses_explicit_as_of_and_aligned_weather_series():
    """Replacing actual weather provenance with request time must fail."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request("wind_power")
    feature_as_of = pd.Timestamp(
        "2026-07-01 09:30",
        tz="Asia/Shanghai",
    )
    request.data = {
        "wind_speed": pd.Series(
            [12.0, 12.0],
            index=_valid_times(request),
        ),
        "feature_as_of": feature_as_of,
        "weather_variable": "wind_speed",
        "weather_unit": "m/s",
    }

    result = RenewablePowerForecastModel(
        target="wind_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 5.0},
    ).forecast(request)

    assert result.point.tolist() == [5.0, 5.0]
    assert result.feature_as_of == feature_as_of
    assert "source:explicit_weather" in result.quality_flags


def test_physical_renewable_rejects_future_or_misaligned_weather():
    """Future or wrong-grid weather must not enter a physical forecast."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    model = RenewablePowerForecastModel(
        target="wind_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 5.0},
    )
    future_request = _request(
        "wind_power",
        data={
            "wind_speed": pd.Series(
                [12.0, 12.0],
                index=pd.date_range(
                    "2026-07-01 10:15",
                    periods=2,
                    freq="15min",
                    tz="Asia/Shanghai",
                ),
            ),
            "feature_as_of": pd.Timestamp(
                "2026-07-01 10:30",
                tz="Asia/Shanghai",
            ),
            "weather_variable": "wind_speed",
            "weather_unit": "m/s",
        },
    )
    misaligned_request = _request(
        "wind_power",
        data={
            "wind_speed": pd.Series(
                [12.0, 12.0],
                index=pd.date_range(
                    "2026-07-01 10:30",
                    periods=2,
                    freq="15min",
                    tz="Asia/Shanghai",
                ),
            ),
            "feature_as_of": pd.Timestamp(
                "2026-07-01 09:30",
                tz="Asia/Shanghai",
            ),
            "weather_variable": "wind_speed",
            "weather_unit": "m/s",
        },
    )

    with pytest.raises(ValueError, match="feature_as_of"):
        model.forecast(future_request)
    with pytest.raises(ValueError, match="valid-time index"):
        model.forecast(misaligned_request)


def test_physical_renewable_accepts_weather_forecast_result_provenance():
    """Dropping a weather ForecastResult's as-of metadata must fail."""
    from ele_trading.forecasting.contracts import ForecastResult
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    renewable_request = _request("wind_power")
    weather_request = _request(
        "weather",
        data={"weather_variable": "wind_speed"},
    )
    index = _valid_times(weather_request)
    weather_point = pd.Series([12.0, 12.0], index=index)
    weather_result = ForecastResult(
        request=weather_request,
        point=weather_point,
        quantiles={
            0.1: weather_point.copy(),
            0.9: weather_point.copy(),
        },
        unit="m/s",
        model_version="weather-v1",
        feature_as_of=pd.Timestamp(
            "2026-07-01 09:45",
            tz="Asia/Shanghai",
        ),
        quality_flags=("source:archived",),
    )
    renewable_request.data = {
        "weather_forecast": weather_result,
        "weather_variable": "wind_speed",
    }

    result = RenewablePowerForecastModel(
        target="wind_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 5.0},
    ).forecast(renewable_request)

    assert result.point.tolist() == [5.0, 5.0]
    assert result.feature_as_of == weather_result.feature_as_of
    assert "source:weather_forecast" in result.quality_flags


def test_physical_renewable_rejects_future_weather_forecast_issue_time():
    """An old feature cutoff must not hide a future weather forecast vintage."""
    from ele_trading.forecasting.contracts import ForecastResult
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    renewable_request = _request(
        "wind_power",
        horizon=1,
        frequency="h",
    )
    weather_request = _request(
        "weather",
        horizon=1,
        frequency="30min",
        issue_time=pd.Timestamp(
            "2026-07-01 10:30",
            tz="Asia/Shanghai",
        ),
        data={"weather_variable": "wind_speed"},
    )
    weather_point = pd.Series(
        [12.0],
        index=_valid_times(weather_request),
    )
    weather_result = ForecastResult(
        request=weather_request,
        point=weather_point,
        quantiles={
            0.1: weather_point.copy(),
            0.9: weather_point.copy(),
        },
        unit="m/s",
        model_version="weather-v1",
        feature_as_of=pd.Timestamp(
            "2026-07-01 09:45",
            tz="Asia/Shanghai",
        ),
    )
    renewable_request.data = {
        "weather_forecast": weather_result,
        "weather_variable": "wind_speed",
    }

    with pytest.raises(ValueError, match="issue_time"):
        RenewablePowerForecastModel(
            target="wind_power",
            mode="physical",
            capacity_by_scope={("site", "north-1"): 5.0},
        ).forecast(renewable_request)


def test_physical_renewable_rejects_duck_typed_weather_forecast():
    """A values/issue_time lookalike must not bypass ForecastResult contracts."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request("wind_power")

    class DuckWeatherForecast:
        values = pd.Series([12.0, 12.0], index=_valid_times(request))
        issue_time = pd.Timestamp(
            "2026-07-01 09:45",
            tz="Asia/Shanghai",
        )

    request.data = {
        "weather_variable": "wind_speed",
        "weather_forecast": DuckWeatherForecast(),
    }

    with pytest.raises(ValueError, match="ForecastResult"):
        RenewablePowerForecastModel(
            target="wind_power",
            mode="physical",
            capacity_by_scope={("site", "north-1"): 5.0},
        ).forecast(request)


@pytest.mark.parametrize(
    ("scope_type", "scope_id"),
    [
        ("site", "south-1"),
        ("region", "north-1"),
    ],
)
def test_physical_renewable_rejects_weather_forecast_for_wrong_scope(
    scope_type: str,
    scope_id: str,
):
    """A weather result for another site or scope must not drive this asset."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request("wind_power")
    request.data = {
        "weather_variable": "wind_speed",
        "weather_forecast": _weather_result(
            request,
            weather_variable="wind_speed",
            unit="m/s",
            scope_type=scope_type,
            scope_id=scope_id,
        ),
    }

    with pytest.raises(ValueError, match="scope"):
        RenewablePowerForecastModel(
            target="wind_power",
            mode="physical",
            capacity_by_scope={("site", "north-1"): 5.0},
        ).forecast(request)


@pytest.mark.parametrize(
    (
        "target",
        "requested_variable",
        "result_variable",
        "result_unit",
    ),
    [
        ("wind_power", "wind_speed", "temperature", "degC"),
        ("wind_power", "wind_speed", "wind_speed", "degC"),
        ("pv_power", "irradiance", "wind_speed", "m/s"),
        ("pv_power", "irradiance", "irradiance", "degC"),
    ],
)
def test_physical_renewable_rejects_wrong_forecast_variable_or_unit(
    target: str,
    requested_variable: str,
    result_variable: str,
    result_unit: str,
):
    """Temperature, wind, or wrong-unit results must not be reinterpreted."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request(target)
    request.data = {
        "weather_variable": requested_variable,
        "weather_forecast": _weather_result(
            request,
            weather_variable=result_variable,
            unit=result_unit,
        ),
    }
    if target == "pv_power":
        request.data["site_timezone"] = "Asia/Shanghai"

    with pytest.raises(ValueError, match="weather_variable|unit"):
        RenewablePowerForecastModel(
            target=target,
            mode="physical",
            capacity_by_scope={("site", "north-1"): 5.0},
        ).forecast(request)


@pytest.mark.parametrize("weather_variable", [None, "temperature"])
def test_physical_renewable_requires_expected_request_weather_variable(
    weather_variable: str | None,
):
    """The renewable request must explicitly name its physical input."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request("wind_power")
    request.data = {
        "weather_forecast": _weather_result(
            request,
            weather_variable="wind_speed",
            unit="m/s",
        ),
    }
    if weather_variable is not None:
        request.data["weather_variable"] = weather_variable

    with pytest.raises(ValueError, match="weather_variable"):
        RenewablePowerForecastModel(
            target="wind_power",
            mode="physical",
            capacity_by_scope={("site", "north-1"): 5.0},
        ).forecast(request)


@pytest.mark.parametrize(
    ("target", "data_key", "weather_variable", "weather_unit"),
    [
        ("wind_power", "wind_speed", "wind_speed", None),
        ("wind_power", "wind_speed", "wind_speed", "degC"),
        ("pv_power", "irradiance", "irradiance", "m/s"),
    ],
)
def test_physical_renewable_explicit_series_requires_canonical_unit(
    target: str,
    data_key: str,
    weather_variable: str,
    weather_unit: str | None,
):
    """An explicit Series still needs a stable variable/unit declaration."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request(target)
    request.data = {
        data_key: pd.Series(
            [12.0, 12.0],
            index=_valid_times(request),
        ),
        "weather_variable": weather_variable,
        "feature_as_of": pd.Timestamp(
            "2026-07-01 09:45",
            tz="Asia/Shanghai",
        ),
    }
    if weather_unit is not None:
        request.data["weather_unit"] = weather_unit
    if target == "pv_power":
        request.data["site_timezone"] = "Asia/Shanghai"

    with pytest.raises(ValueError, match="weather_unit"):
        RenewablePowerForecastModel(
            target=target,
            mode="physical",
            capacity_by_scope={("site", "north-1"): 5.0},
        ).forecast(request)


def test_pv_night_rule_uses_explicit_site_timezone_not_display_timezone():
    """The same instants must not change PV output when index timezone changes."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    asia_request = _request(
        "pv_power",
        horizon=1,
        frequency="h",
        issue_time=pd.Timestamp(
            "2026-07-01 11:00",
            tz="Asia/Shanghai",
        ),
    )
    utc_request = _request(
        "pv_power",
        horizon=1,
        frequency="h",
        issue_time=pd.Timestamp(
            "2026-07-01 03:00",
            tz="UTC",
        ),
    )
    feature_as_of = pd.Timestamp("2026-07-01 02:00", tz="UTC")
    asia_request.data = {
        "irradiance": pd.Series(
            [1000.0],
            index=_valid_times(asia_request),
        ),
        "feature_as_of": feature_as_of,
        "site_timezone": "Asia/Shanghai",
        "weather_variable": "irradiance",
        "weather_unit": "W/m2",
    }
    utc_request.data = {
        "irradiance": pd.Series(
            [1000.0],
            index=_valid_times(utc_request),
        ),
        "feature_as_of": feature_as_of,
        "site_timezone": "Asia/Shanghai",
        "weather_variable": "irradiance",
        "weather_unit": "W/m2",
    }
    model = RenewablePowerForecastModel(
        target="pv_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 4.0},
    )

    asia = model.forecast(asia_request)
    utc = model.forecast(utc_request)

    assert asia.point.tolist() == [4.0]
    assert utc.point.tolist() == asia.point.tolist()


def test_pv_forecast_requires_explicit_site_timezone():
    """Falling back to the request index timezone must remain impossible."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request("pv_power")
    request.data = {
        "irradiance": pd.Series(
            [1000.0, 1000.0],
            index=_valid_times(request),
        ),
        "feature_as_of": pd.Timestamp(
            "2026-07-01 09:30",
            tz="Asia/Shanghai",
        ),
        "weather_variable": "irradiance",
        "weather_unit": "W/m2",
    }

    with pytest.raises(ValueError, match="site_timezone"):
        RenewablePowerForecastModel(
            target="pv_power",
            mode="physical",
            capacity_by_scope={("site", "north-1"): 4.0},
        ).forecast(request)


def test_wind_compatibility_api_uses_height_shear_and_equivalent_hours():
    """Ignoring accepted wind physics parameters must not preserve output."""
    from ele_trading.forecasting.wind_forecast import WindPowerForecaster

    weather = pd.DataFrame(
        {"wind_speed": [4.0, 8.0, 12.0]},
        index=pd.date_range(
            "2026-07-01 10:00",
            periods=3,
            freq="h",
            tz="Asia/Shanghai",
        ),
    )
    low_hub = WindPowerForecaster(
        mode="physics",
        hub_height=10.0,
        wind_speed_ref_height=10.0,
        wind_shear_exp=0.2,
    ).forecast_from_weather(
        weather,
        capacity_mw=5.0,
        equiv_hours=2000.0,
    )
    high_hub = WindPowerForecaster(
        mode="physics",
        hub_height=100.0,
        wind_speed_ref_height=10.0,
        wind_shear_exp=0.2,
    ).forecast_from_weather(
        weather,
        capacity_mw=5.0,
        equiv_hours=2000.0,
    )
    low_equiv = WindPowerForecaster(
        mode="physics",
        hub_height=10.0,
        wind_speed_ref_height=10.0,
        wind_shear_exp=0.2,
    ).forecast_from_weather(
        weather,
        capacity_mw=5.0,
        equiv_hours=1000.0,
    )

    assert high_hub.point_forecast != pytest.approx(
        low_hub.point_forecast
    )
    assert low_equiv.point_forecast != pytest.approx(
        low_hub.point_forecast
    )


def test_pv_compatibility_api_rejects_unsupported_geometry_parameters():
    """Accepting latitude/longitude/tilt without using them must fail."""
    from ele_trading.forecasting.pv_forecast import PVPowerForecaster

    with pytest.raises(ValueError, match="not supported"):
        PVPowerForecaster(
            mode="physics",
            latitude=40.0,
            longitude=110.0,
        )
    with pytest.raises(ValueError, match="not supported"):
        PVPowerForecaster(
            mode="physics",
            tilt=30.0,
        )


def test_pv_compatibility_api_uses_equivalent_hours():
    """Ignoring equivalent-hours calibration must preserve the wrong output."""
    from ele_trading.forecasting.pv_forecast import PVPowerForecaster

    weather = pd.DataFrame(
        {"ghi": [200.0, 600.0, 1000.0]},
        index=pd.date_range(
            "2026-07-01 10:00",
            periods=3,
            freq="h",
            tz="Asia/Shanghai",
        ),
    )
    model = PVPowerForecaster(
        mode="physics",
        timezone="Asia/Shanghai",
    )

    low = model.forecast_from_weather(
        weather,
        capacity_mw=4.0,
        equiv_hours=1000.0,
    )
    high = model.forecast_from_weather(
        weather,
        capacity_mw=4.0,
        equiv_hours=3000.0,
    )

    assert low.point_forecast != pytest.approx(high.point_forecast)


def test_weather_history_rejects_unsorted_time_axis():
    """Using iloc on an unsorted history must not pick the wrong observation."""
    from ele_trading.forecasting.weather_forecast import WeatherBaselineModel

    history = pd.Series(
        [20.0, 19.0],
        index=pd.DatetimeIndex(
            [
                "2026-07-01 09:45+08:00",
                "2026-07-01 09:30+08:00",
            ]
        ),
    )

    with pytest.raises(ValueError, match="monotonic"):
        WeatherBaselineModel(
            history_by_scope={("site", "north-1"): history},
            unit_by_scope={("site", "north-1"): "degC"},
        ).forecast(_request("weather"))


def test_price_history_rejects_duplicate_time_axis():
    """Duplicate price timestamps must not silently select one observation."""
    from ele_trading.forecasting.price_forecast import PriceForecastModel

    history = pd.Series(
        [100.0, 110.0, 120.0],
        index=pd.DatetimeIndex(
            [
                "2026-07-01 09:30+08:00",
                "2026-07-01 09:45+08:00",
                "2026-07-01 09:45+08:00",
            ]
        ),
    )

    with pytest.raises(ValueError, match="duplicate"):
        PriceForecastModel(
            history_by_scope={"real_time_reference": history},
        ).forecast(
            _request(
                "price",
                data={"market_scope": "real_time_reference"},
            )
        )


def test_load_history_rejects_irregular_or_mismatched_frequency():
    """AR must not fit one step size and recurse at another step size."""
    from ele_trading.forecasting.load_forecast import LoadForecastModel

    regular_hourly = pd.Series(
        range(30),
        index=pd.date_range(
            "2026-06-30 04:00",
            periods=30,
            freq="h",
            tz="Asia/Shanghai",
        ),
        dtype=float,
    )
    irregular_index = regular_hourly.index.delete(10).append(
        pd.DatetimeIndex(
            [regular_hourly.index[-1] + pd.Timedelta(hours=1)]
        )
    )
    irregular = pd.Series(
        range(30),
        index=irregular_index,
        dtype=float,
    )
    model = LoadForecastModel(
        history_by_scope={
            ("site", "irregular"): irregular,
            ("site", "hourly"): regular_hourly,
        },
        ar_lags=4,
    )

    with pytest.raises(ValueError, match="regular"):
        model.forecast(
            _request("load", scope_id="irregular", frequency="h")
        )
    with pytest.raises(ValueError, match="frequency"):
        model.forecast(
            _request("load", scope_id="hourly", frequency="15min")
        )


def test_statistical_renewable_reports_actual_history_source_and_as_of():
    """Replacing the participating observation time with request time must fail."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    history = pd.Series(
        [2.0, 3.0, 4.0],
        index=pd.date_range(
            "2026-07-01 09:15",
            periods=3,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    result = RenewablePowerForecastModel(
        target="wind_power",
        mode="statistical",
        capacity_by_scope={("site", "north-1"): 5.0},
        history_by_scope={("site", "north-1"): history},
    ).forecast(_request("wind_power"))

    assert result.feature_as_of == history.index[-1]
    assert "source:historical_output" in result.quality_flags


@pytest.mark.parametrize(
    "history",
    [
        None,
        pd.Series(
            [],
            index=pd.DatetimeIndex([], tz="Asia/Shanghai"),
            dtype=float,
        ),
    ],
)
def test_simple_provider_rejects_missing_or_empty_price_history(history):
    """A hidden flat-300 default must not produce an untraceable forecast."""
    from ele_trading.forecasting.price_forecast import SimplePriceForecaster
    from ele_trading.forecasting.provider import SimpleForecastProvider

    provider = SimpleForecastProvider(
        SimplePriceForecaster(),
        None,
        default_history_prices=history,
    )

    with pytest.raises(ValueError, match="price history"):
        provider.get_price_forecast(
            _request(
                "price",
                scope_type="market",
                scope_id="day_ahead_reference",
            )
        )


def test_simple_provider_uses_actual_price_history_as_of():
    """Replacing the last participating history time with request time must fail."""
    from ele_trading.forecasting.price_forecast import SimplePriceForecaster
    from ele_trading.forecasting.provider import SimpleForecastProvider

    history = pd.Series(
        [280.0, 300.0, 320.0],
        index=pd.date_range(
            "2026-07-01 09:15",
            periods=3,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    result = SimpleForecastProvider(
        SimplePriceForecaster(),
        None,
        default_history_prices=history,
    ).get_price_forecast(
        _request(
            "price",
            scope_type="market",
            scope_id="day_ahead_reference",
        )
    )

    assert result.feature_as_of == history.index[-1]
    assert "source:historical_price" in result.quality_flags


def test_simple_provider_load_validates_fitted_history_axis():
    """The compatibility path must not recurse hourly fit state at 15 minutes."""
    from ele_trading.forecasting.load_forecast import LoadForecaster
    from ele_trading.forecasting.provider import SimpleForecastProvider

    history = pd.Series(
        range(30),
        index=pd.date_range(
            "2026-06-30 04:00",
            periods=30,
            freq="h",
            tz="Asia/Shanghai",
        ),
        dtype=float,
    )
    provider = SimpleForecastProvider(
        None,
        LoadForecaster(ar_lags=4).fit(history),
    )

    with pytest.raises(ValueError, match="frequency"):
        provider.get_load_forecast(
            _request(
                "load",
                frequency="15min",
                issue_time=pd.Timestamp(
                    "2026-07-02 00:00",
                    tz="Asia/Shanghai",
                ),
            )
        )


def test_arima_adapter_runs_through_registry_generic_provider():
    """Keeping ARIMA on predict(horizon) only must fail generic resolution."""
    import numpy as np

    from ele_trading.forecasting.price_forecast import ARIMAForecastModel
    from ele_trading.forecasting.provider import ForecastProvider
    from ele_trading.forecasting.registry import ForecastModelRegistry

    history = pd.Series(
        [
            300.0 + 15.0 * np.sin(step * 0.3)
            for step in range(40)
        ],
        index=pd.date_range(
            "2026-07-01 00:00",
            periods=40,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    model = ARIMAForecastModel(
        history=history,
        order=(1, 0, 0),
        market_scope="real_time_reference",
    )
    registry = ForecastModelRegistry()
    registry.register(
        "price",
        "arima",
        model.model_version,
        model,
        default=True,
    )
    request = _request(
        "price",
        scope_type="market",
        scope_id="mengxi",
        data={"market_scope": "real_time_reference"},
        model_name="arima",
        model_version=model.model_version,
    )

    result = ForecastProvider(registry).forecast(request)

    assert result.request is request
    assert result.point.index.equals(_valid_times(request))
    assert tuple(result.quantiles) == (0.1, 0.9)
    assert all(result.quantiles[0.1] <= result.point)
    assert all(result.point <= result.quantiles[0.9])
    assert result.feature_as_of == history.index[-1]
    assert result.model_version == model.model_version
    assert "source:historical_price" in result.quality_flags


def test_arima_adapter_rejects_lookahead_or_frequency_mismatch():
    """A fitted ARIMA must not serve requests before or off its training grid."""
    from ele_trading.forecasting.price_forecast import ARIMAForecastModel

    history = pd.Series(
        range(20),
        index=pd.date_range(
            "2026-07-01 06:00",
            periods=20,
            freq="15min",
            tz="Asia/Shanghai",
        ),
        dtype=float,
    )
    model = ARIMAForecastModel(
        history=history,
        order=(1, 0, 0),
        market_scope="real_time_reference",
    )

    with pytest.raises(ValueError, match="issue_time"):
        model.forecast(
            _request(
                "price",
                issue_time=pd.Timestamp(
                    "2026-07-01 09:00",
                    tz="Asia/Shanghai",
                ),
                data={"market_scope": "real_time_reference"},
            )
        )
    with pytest.raises(ValueError, match="frequency"):
        model.forecast(
            _request(
                "price",
                frequency="h",
                issue_time=pd.Timestamp(
                    "2026-07-01 11:00",
                    tz="Asia/Shanghai",
                ),
                data={"market_scope": "real_time_reference"},
            )
        )


def test_arima_request_model_is_exported_from_forecasting_package():
    """A request-oriented model hidden from the public package is not usable API."""
    from ele_trading import forecasting
    from ele_trading.forecasting.price_forecast import ARIMAForecastModel

    assert forecasting.ARIMAForecastModel is ARIMAForecastModel
