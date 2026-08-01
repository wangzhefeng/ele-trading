"""Phase 3 complete forecasting behavior."""

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


def test_weather_archive_selects_latest_vintage_not_after_issue_time():
    """Selecting a future or stale vintage must change the returned values."""
    from ele_trading.forecasting.weather_forecast import (
        ArchivedWeatherForecastAdapter,
        WeatherBaselineModel,
    )

    archive = ArchivedWeatherForecastAdapter()
    valid_times = pd.date_range(
        "2026-07-01 10:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    archive.add(
        scope_type="site",
        scope_id="north-1",
        issue_time=pd.Timestamp("2026-07-01 09:00", tz="Asia/Shanghai"),
        values=pd.Series([21.0, 22.0], index=valid_times),
        unit="degC",
    )
    archive.add(
        scope_type="site",
        scope_id="north-1",
        issue_time=pd.Timestamp("2026-07-01 10:30", tz="Asia/Shanghai"),
        values=pd.Series([99.0, 99.0], index=valid_times),
        unit="degC",
    )

    result = WeatherBaselineModel(archive=archive).forecast(
        _request("weather")
    )

    assert result.point.tolist() == [21.0, 22.0]
    assert result.feature_as_of == pd.Timestamp(
        "2026-07-01 09:00",
        tz="Asia/Shanghai",
    )
    assert "source:archived" in result.quality_flags


def test_weather_persistence_is_deterministic_and_applies_configured_bias():
    """Dropping persistence state or bias correction must change the forecast."""
    from ele_trading.forecasting.weather_forecast import WeatherBaselineModel

    history = pd.Series(
        [18.0, 20.0],
        index=pd.date_range(
            "2026-07-01 09:30",
            periods=2,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    model = WeatherBaselineModel(
        history_by_scope={("site", "north-1"): history},
        baseline="persistence",
        bias_correction=1.5,
        unit_by_scope={("site", "north-1"): "degC"},
    )

    first = model.forecast(_request("weather"))
    second = model.forecast(_request("weather"))

    assert first.point.tolist() == [21.5, 21.5]
    assert second.point.equals(first.point)
    assert tuple(first.quantiles) == (0.1, 0.9)
    assert all(first.quantiles[0.1] == first.point)
    assert all(first.quantiles[0.9] == first.point)
    assert "baseline:persistence" in first.quality_flags
    assert "bias_corrected" in first.quality_flags


def test_weather_climatology_uses_matching_time_slots_only():
    """Replacing slot climatology with an overall mean must fail this forecast."""
    from ele_trading.forecasting.weather_forecast import WeatherBaselineModel

    index = pd.date_range(
        "2026-06-29 10:15",
        "2026-06-30 10:30",
        freq="15min",
        tz="Asia/Shanghai",
    )
    history = pd.Series(0.0, index=index)
    history.loc["2026-06-29 10:15"] = 10.0
    history.loc["2026-06-29 10:30"] = 20.0
    history.loc["2026-06-30 10:15"] = 12.0
    history.loc["2026-06-30 10:30"] = 22.0
    result = WeatherBaselineModel(
        history_by_scope={("site", "north-1"): history},
        baseline="climatology",
        unit_by_scope={("site", "north-1"): "degC"},
    ).forecast(_request("weather"))

    assert result.point.tolist() == [11.0, 21.0]
    assert "baseline:climatology" in result.quality_flags


def test_weather_baseline_rejects_missing_history_and_source():
    """Returning invented weather without a source must remain impossible."""
    from ele_trading.forecasting.weather_forecast import WeatherBaselineModel

    with pytest.raises(ValueError, match="weather history"):
        WeatherBaselineModel().forecast(_request("weather"))


def test_price_scope_comes_from_request_data_not_horizon():
    """Using one horizon heuristic for day-ahead and real-time must fail."""
    from ele_trading.forecasting.price_forecast import PriceForecastModel

    index = pd.date_range(
        "2026-06-30 00:15",
        periods=96,
        freq="15min",
        tz="Asia/Shanghai",
    )
    model = PriceForecastModel(
        history_by_scope={
            "day_ahead_reference": pd.Series(300.0, index=index),
            "real_time_reference": pd.Series(450.0, index=index),
        },
        method="seasonal_naive",
    )

    day_ahead = model.forecast(
        _request(
            "price",
            data={"market_scope": "day_ahead_reference"},
        )
    )
    real_time = model.forecast(
        _request(
            "price",
            data={"market_scope": "real_time_reference"},
        )
    )

    assert day_ahead.point.tolist() == [300.0, 300.0]
    assert real_time.point.tolist() == [450.0, 450.0]
    assert day_ahead.unit == "CNY/MWh"


def test_price_forecast_rejects_missing_explicit_market_scope():
    """Guessing price scope from scope_id or frequency must remain impossible."""
    from ele_trading.forecasting.price_forecast import PriceForecastModel

    with pytest.raises(ValueError, match="market_scope"):
        PriceForecastModel(history_by_scope={}).forecast(
            _request("price")
        )


def test_price_monthly_seasonal_naive_outputs_requested_quantiles():
    """Dropping monthly support or quantile alignment must fail this scenario."""
    from ele_trading.forecasting.price_forecast import PriceForecastModel

    history_index = pd.date_range(
        "2025-01-01",
        periods=18,
        freq="MS",
        tz="Asia/Shanghai",
    )
    history = pd.Series(
        [100.0 + timestamp.month for timestamp in history_index],
        index=history_index,
    )
    request = _request(
        "price",
        scope_type="market",
        scope_id="mengxi",
        horizon=2,
        frequency="MS",
        issue_time=pd.Timestamp("2026-06-15", tz="Asia/Shanghai"),
        data={"market_scope": "mid_long_term"},
    )

    result = PriceForecastModel(
        history_by_scope={"mid_long_term": history},
        method="seasonal_naive",
    ).forecast(request)

    assert result.point.tolist() == [107.0, 108.0]
    assert result.point.index.equals(
        pd.date_range(
            "2026-07-01",
            periods=2,
            freq="MS",
            tz="Asia/Shanghai",
        )
    )
    assert tuple(result.quantiles) == (0.1, 0.9)
    assert all(result.quantiles[0.1] <= result.point)
    assert all(result.point <= result.quantiles[0.9])


def test_price_regression_baseline_preserves_trend():
    """Replacing regression with a flat overall mean must fail this forecast."""
    from ele_trading.forecasting.price_forecast import PriceForecastModel

    index = pd.date_range(
        "2026-06-30 08:00",
        periods=8,
        freq="15min",
        tz="Asia/Shanghai",
    )
    history = pd.Series(
        [100.0 + 2.0 * step for step in range(8)],
        index=index,
    )
    result = PriceForecastModel(
        history_by_scope={"real_time_reference": history},
        method="regression",
    ).forecast(
        _request(
            "price",
            issue_time=index[-1],
            data={"market_scope": "real_time_reference"},
        )
    )

    assert result.point.tolist() == pytest.approx([116.0, 118.0])
    assert result.model_version == "price-regression-v1"


def test_load_fitted_ar_state_changes_forecast_from_climatology():
    """Returning only the time-slot climatology must fail this AR scenario."""
    import numpy as np

    from ele_trading.forecasting.load_forecast import LoadForecaster

    index = pd.date_range(
        "2026-01-01",
        periods=24 * 20,
        freq="h",
        tz="Asia/Shanghai",
    )
    values = [80.0]
    for step in range(1, len(index)):
        values.append(
            12.0
            + 0.86 * values[-1]
            + 4.0 * np.sin(2.0 * np.pi * step / 24.0)
        )
    history = pd.Series(values, index=index)
    next_time = index[-1] + pd.Timedelta(hours=1)
    slot_values = history.loc[
        (history.index.month == next_time.month)
        & (history.index.hour == next_time.hour)
    ]
    climatology_only = float(slot_values.mean())

    result = LoadForecaster(ar_lags=6).fit(history).predict(
        horizon=3,
        start_time=next_time,
        frequency="h",
    )

    assert result.point_forecast[0] != pytest.approx(climatology_only)
    assert result.point_forecast[1] != pytest.approx(
        float(
            history.loc[
                (history.index.month == (next_time + pd.Timedelta(hours=1)).month)
                & (history.index.hour == (next_time + pd.Timedelta(hours=1)).hour)
            ].mean()
        )
    )


@pytest.mark.parametrize(
    "scope_type",
    ["system", "region", "node", "portfolio", "site"],
)
def test_load_model_supports_scopes_and_flags_insufficient_history(
    scope_type: str,
):
    """Silent use of a short load history must fail for every supported scope."""
    from ele_trading.forecasting.load_forecast import LoadForecastModel

    history = pd.Series(
        [10.0, 11.0, 12.0],
        index=pd.date_range(
            "2026-07-01 09:15",
            periods=3,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    request = _request(
        "load",
        scope_type=scope_type,
        scope_id="scope-1",
    )

    result = LoadForecastModel(
        history_by_scope={(scope_type, "scope-1"): history},
        ar_lags=4,
    ).forecast(request)

    assert result.point.tolist() == [12.0, 12.0]
    assert result.unit == "MW"
    assert "degraded:insufficient_history" in result.quality_flags


def test_bottom_up_reconciliation_sums_children_exactly():
    """An aggregate copied from a stale base forecast must fail consistency."""
    from ele_trading.forecasting.load_forecast import bottom_up_reconcile

    index = pd.date_range(
        "2026-07-01 10:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    reconciled = bottom_up_reconcile(
        {
            "north": pd.Series([7.0, 8.0], index=index),
            "south": pd.Series([5.0, 6.0], index=index),
        },
        {"system": ("north", "south")},
    )

    assert reconciled["system"].tolist() == [12.0, 14.0]
    assert reconciled["system"].equals(
        reconciled["north"] + reconciled["south"]
    )


def test_bottom_up_reconciliation_overwrites_stale_aggregate_input():
    """Keeping a supplied inconsistent aggregate must fail bottom-up semantics."""
    from ele_trading.forecasting.load_forecast import bottom_up_reconcile

    index = pd.date_range(
        "2026-07-01 10:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    reconciled = bottom_up_reconcile(
        {
            "north": pd.Series([7.0, 8.0], index=index),
            "south": pd.Series([5.0, 6.0], index=index),
            "system": pd.Series([99.0, 99.0], index=index),
        },
        {"system": ("north", "south")},
    )

    assert reconciled["system"].tolist() == [12.0, 14.0]


@pytest.mark.parametrize("method", ["least_squares", "constrained"])
def test_reconciliation_projection_enforces_aggregate_consistency(method: str):
    """Leaving independently forecast aggregates inconsistent must fail."""
    from ele_trading.forecasting.load_forecast import reconcile_hierarchy

    index = pd.date_range(
        "2026-07-01 10:15",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    base = pd.DataFrame(
        {
            "system": [20.0, 16.0],
            "north": [7.0, 9.0],
            "south": [8.0, 5.0],
        },
        index=index,
    )
    summing = pd.DataFrame(
        {
            "north": [1.0, 1.0, 0.0],
            "south": [1.0, 0.0, 1.0],
        },
        index=["system", "north", "south"],
    )

    reconciled = reconcile_hierarchy(
        base,
        summing,
        method=method,
    )

    assert reconciled["system"].to_numpy() == pytest.approx(
        (reconciled["north"] + reconciled["south"]).to_numpy()
    )
    if method == "constrained":
        assert (reconciled >= 0.0).all().all()


def test_wind_physical_curve_enforces_cut_points_capacity_and_availability():
    """Ignoring turbine cut points or availability must fail MW output bounds."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request("wind_power", horizon=6)
    request.data = {
        "wind_speed": pd.Series(
            [2.0, 3.0, 7.5, 12.0, 20.0, 25.0],
            index=_valid_times(request),
        ),
        "availability": [1.0, 1.0, 1.0, 0.5, 0.5, 1.0],
        "feature_as_of": pd.Timestamp(
            "2026-07-01 09:45",
            tz="Asia/Shanghai",
        ),
        "weather_variable": "wind_speed",
        "weather_unit": "m/s",
    }
    result = RenewablePowerForecastModel(
        target="wind_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 10.0},
    ).forecast(request)

    expected_mid = 10.0 * (
        (7.5**3 - 3.0**3) / (12.0**3 - 3.0**3)
    )
    assert result.point.tolist() == pytest.approx(
        [0.0, 0.0, expected_mid, 5.0, 5.0, 0.0]
    )
    assert result.unit == "MW"
    assert result.point.between(0.0, 10.0).all()


def test_pv_physical_forecast_is_zero_at_night_and_availability_bounded():
    """Positive irradiance must not produce PV power during local night."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request(
        "pv_power",
        horizon=12,
        frequency="h",
        issue_time=pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai"),
    )
    request.data = {
        "irradiance": pd.Series(
            [1000.0] * 12,
            index=_valid_times(request),
        ),
        "availability": 0.5,
        "feature_as_of": pd.Timestamp(
            "2026-06-30 23:00",
            tz="Asia/Shanghai",
        ),
        "site_timezone": "Asia/Shanghai",
        "weather_variable": "irradiance",
        "weather_unit": "W/m2",
    }
    result = RenewablePowerForecastModel(
        target="pv_power",
        mode="physical",
        capacity_by_scope={("site", "north-1"): 4.0},
    ).forecast(request)

    assert result.point.iloc[:5].tolist() == [0.0] * 5
    assert result.point.iloc[5:].tolist() == [2.0] * 7
    assert result.point.max() == 2.0


def test_renewable_external_path_clips_invalid_provider_values():
    """Passing through negative or over-capacity external values must fail."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewableForecastVintage,
        RenewablePowerForecastModel,
    )

    class Adapter:
        def get_forecast(self, request):
            return RenewableForecastVintage(
                issue_time=pd.Timestamp(
                    "2026-07-01 09:45",
                    tz="Asia/Shanghai",
                ),
                values=pd.Series(
                    [-2.0, 20.0],
                    index=pd.date_range(
                        "2026-07-01 10:15",
                        periods=2,
                        freq="15min",
                        tz="Asia/Shanghai",
                    ),
                ),
            )

    result = RenewablePowerForecastModel(
        target="wind_power",
        mode="external",
        capacity_by_scope={("site", "north-1"): 5.0},
        adapter=Adapter(),
    ).forecast(
        _request(
            "wind_power",
            data={"availability": 0.8},
        )
    )

    assert result.point.tolist() == [0.0, 4.0]
    assert result.feature_as_of == pd.Timestamp(
        "2026-07-01 09:45",
        tz="Asia/Shanghai",
    )
    assert "source:external" in result.quality_flags


def test_renewable_statistical_baseline_is_visible_and_bounded():
    """A hidden overall-mean fallback must fail this persistence baseline."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    history = pd.Series(
        [1.0, 8.0],
        index=pd.date_range(
            "2026-07-01 09:30",
            periods=2,
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

    assert result.point.tolist() == [5.0, 5.0]
    assert "baseline:statistical-persistence" in result.quality_flags


def test_renewable_portfolio_aggregation_sums_site_forecasts():
    """Forecasting a portfolio as one invented site must fail aggregation."""
    from ele_trading.forecasting.renewable_forecast import (
        RenewablePowerForecastModel,
    )

    request = _request(
        "wind_power",
        scope_type="portfolio",
        scope_id="portfolio-1",
    )
    valid_times = _valid_times(request)
    feature_as_of = pd.Timestamp(
        "2026-07-01 09:45",
        tz="Asia/Shanghai",
    )
    request.data = {
        "site_data": {
            "site-a": {
                "wind_speed": pd.Series(
                    [12.0, 12.0],
                    index=valid_times,
                ),
                "feature_as_of": feature_as_of,
                "weather_variable": "wind_speed",
                "weather_unit": "m/s",
            },
            "site-b": {
                "wind_speed": pd.Series(
                    [12.0, 25.0],
                    index=valid_times,
                ),
                "feature_as_of": feature_as_of,
                "weather_variable": "wind_speed",
                "weather_unit": "m/s",
            },
        }
    }
    result = RenewablePowerForecastModel(
        target="wind_power",
        mode="physical",
        capacity_by_scope={
            ("site", "site-a"): 3.0,
            ("site", "site-b"): 4.0,
        },
        members_by_scope={
            ("portfolio", "portfolio-1"): ("site-a", "site-b"),
        },
    ).forecast(request)

    assert result.point.tolist() == [7.0, 3.0]
    assert result.point.max() <= 7.0
    assert "aggregate:bottom_up" in result.quality_flags


def test_legacy_wind_and_pv_physical_paths_no_longer_need_deleted_package():
    """Calling physical compatibility APIs must not import capacity_planning."""
    from ele_trading.forecasting.pv_forecast import PVPowerForecaster
    from ele_trading.forecasting.wind_forecast import WindPowerForecaster

    index = pd.date_range(
        "2026-07-01 12:00",
        periods=3,
        freq="h",
        tz="Asia/Shanghai",
    )
    wind = WindPowerForecaster(mode="physics").forecast_from_weather(
        pd.DataFrame(
            {"wind_speed": [2.0, 12.0, 25.0]},
            index=index,
        ),
        capacity_mw=5.0,
        equiv_hours=2920.0,
    )
    pv = PVPowerForecaster(
        mode="physics",
        timezone="Asia/Shanghai",
    ).forecast_from_weather(
        pd.DataFrame(
            {"ghi": [0.0, 1000.0, 500.0]},
            index=index,
        ),
        capacity_mw=4.0,
        equiv_hours=4380.0,
    )

    assert wind.point_forecast == pytest.approx([0.0, 5.0, 0.0])
    assert pv.point_forecast == pytest.approx([0.0, 4.0, 2.0])


def test_registry_resolves_exact_target_model_and_version():
    """Resolving a different model version must change the real forecast result."""
    from ele_trading.forecasting.contracts import (
        ForecastResult,
        _valid_time_index,
    )
    from ele_trading.forecasting.registry import ForecastModelRegistry

    class ConstantModel:
        def __init__(self, value: float, version: str) -> None:
            self.value = value
            self.version = version

        def forecast(self, request):
            index = _valid_time_index(request)
            point = pd.Series(self.value, index=index, dtype=float)
            return ForecastResult(
                request=request,
                point=point,
                quantiles={
                    level: point.copy()
                    for level in request.quantiles
                },
                unit="MW",
                model_version=self.version,
                feature_as_of=request.issue_time,
            )

    registry = ForecastModelRegistry()
    registry.register(
        "load",
        "constant",
        "v1",
        ConstantModel(1.0, "v1"),
    )
    registry.register(
        "load",
        "constant",
        "v2",
        ConstantModel(2.0, "v2"),
        default=True,
    )

    exact = registry.resolve("load", "constant", "v1")
    default = registry.resolve("load", "default", None)

    assert exact.forecast(_request("load")).point.tolist() == [1.0, 1.0]
    assert default.forecast(_request("load")).point.tolist() == [2.0, 2.0]


def test_registry_rejects_unknown_target_and_missing_model_explicitly():
    """A silent overall-mean fallback must not mask registry mistakes."""
    from ele_trading.forecasting.registry import (
        ForecastModelNotFoundError,
        ForecastModelRegistry,
        UnknownForecastTargetError,
    )

    registry = ForecastModelRegistry()

    with pytest.raises(UnknownForecastTargetError, match="unknown"):
        registry.resolve("unknown", "default", None)
    with pytest.raises(ForecastModelNotFoundError, match="price"):
        registry.resolve("price", "default", None)


def test_provider_generic_path_supports_every_target_and_typed_methods_delegate():
    """A target-specific provider branch that skips generic resolution must fail."""
    from ele_trading.forecasting.contracts import (
        ForecastResult,
        _valid_time_index,
    )
    from ele_trading.forecasting.provider import ForecastProvider
    from ele_trading.forecasting.registry import ForecastModelRegistry

    class TargetModel:
        def forecast(self, request):
            index = _valid_time_index(request)
            point = pd.Series(
                float(len(request.target)),
                index=index,
            )
            return ForecastResult(
                request=request,
                point=point,
                quantiles={
                    level: point.copy()
                    for level in request.quantiles
                },
                unit="MW" if request.target != "price" else "CNY/MWh",
                model_version=f"{request.target}-v1",
                feature_as_of=request.issue_time,
            )

    registry = ForecastModelRegistry()
    targets = ("weather", "price", "load", "wind_power", "pv_power")
    for target in targets:
        registry.register(
            target,
            "target-model",
            "v1",
            TargetModel(),
            default=True,
        )
    provider = ForecastProvider(registry)
    typed_methods = {
        "weather": provider.get_weather_forecast,
        "price": provider.get_price_forecast,
        "load": provider.get_load_forecast,
        "wind_power": provider.get_wind_power_forecast,
        "pv_power": provider.get_pv_power_forecast,
    }

    for target in targets:
        request = _request(target)
        generic = provider.forecast(request)
        typed = typed_methods[target](request)
        assert generic.request is request
        assert typed.point.equals(generic.point)
        assert typed.model_version == f"{target}-v1"


def test_provider_uses_requested_model_version_without_fallback():
    """Ignoring request model identity must not select a default model silently."""
    from ele_trading.forecasting.provider import ForecastProvider
    from ele_trading.forecasting.registry import (
        ForecastModelNotFoundError,
        ForecastModelRegistry,
    )

    provider = ForecastProvider(ForecastModelRegistry())
    request = _request(
        "price",
        model_name="seasonal",
        model_version="missing-v9",
    )

    with pytest.raises(ForecastModelNotFoundError, match="missing-v9"):
        provider.forecast(request)


def test_error_and_pinball_metrics_keep_target_unit_and_grain():
    """Dropping unit/grain metadata or using one loss formula must fail."""
    import math

    from ele_trading.forecasting.metrics import (
        mean_absolute_error,
        pinball_loss,
        root_mean_squared_error,
    )

    actual = [1.0, 3.0, 2.0]
    predicted = [2.0, 1.0, 2.0]
    mae = mean_absolute_error(
        actual,
        predicted,
        unit="MW",
        grain="15min",
    )
    rmse = root_mean_squared_error(
        actual,
        predicted,
        unit="MW",
        grain="15min",
    )
    pinball = pinball_loss(
        actual,
        [0.0, 4.0, 2.0],
        quantile=0.9,
        unit="MW",
        grain="15min",
    )

    assert mae.value == pytest.approx(1.0)
    assert rmse.value == pytest.approx(math.sqrt(5.0 / 3.0))
    assert pinball.value == pytest.approx(1.0 / 3.0)
    assert mae.unit == "MW"
    assert mae.target_unit == "MW"
    assert mae.grain == "15min"


def test_coverage_and_direction_metrics_return_ratio_with_target_metadata():
    """Returning percentages without unit semantics must fail evaluation."""
    from ele_trading.forecasting.metrics import (
        direction_accuracy,
        interval_coverage,
    )

    coverage = interval_coverage(
        [1.0, 3.0, 2.0],
        [0.0, 2.0, 2.1],
        [2.0, 4.0, 3.0],
        unit="CNY/MWh",
        grain="monthly",
    )
    direction = direction_accuracy(
        [1.0, 3.0, 2.0, 4.0],
        [1.0, 2.0, 3.0, 4.0],
        unit="CNY/MWh",
        grain="monthly",
    )

    assert coverage.value == pytest.approx(2.0 / 3.0)
    assert direction.value == pytest.approx(2.0 / 3.0)
    assert coverage.unit == "ratio"
    assert coverage.target_unit == "CNY/MWh"
    assert direction.grain == "monthly"


def test_metrics_reject_misaligned_or_invalid_inputs():
    """Silent truncation of evaluation arrays must remain impossible."""
    from ele_trading.forecasting.metrics import (
        interval_coverage,
        mean_absolute_error,
        pinball_loss,
    )

    with pytest.raises(ValueError, match="same non-zero length"):
        mean_absolute_error(
            [1.0],
            [1.0, 2.0],
            unit="MW",
            grain="15min",
        )
    with pytest.raises(ValueError, match="quantile"):
        pinball_loss(
            [1.0],
            [1.0],
            quantile=1.0,
            unit="MW",
            grain="15min",
        )
    with pytest.raises(ValueError, match="lower"):
        interval_coverage(
            [1.0],
            [2.0],
            [0.0],
            unit="MW",
            grain="15min",
        )


def test_phase3_forecasting_apis_are_publicly_exported():
    """Hiding Phase 3 APIs in implementation modules must fail consumers."""
    from ele_trading import forecasting

    for name in (
        "ForecastProvider",
        "ForecastModelRegistry",
        "WeatherBaselineModel",
        "PriceForecastModel",
        "LoadForecastModel",
        "RenewablePowerForecastModel",
        "ForecastMetric",
        "mean_absolute_error",
        "reconcile_hierarchy",
    ):
        assert getattr(forecasting, name) is not None
