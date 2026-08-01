"""Phase 2 forecast and market-data contracts."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "ele_trading"


def _forecast_valid_index(request) -> pd.DatetimeIndex:
    return pd.date_range(
        request.issue_time,
        periods=request.horizon + 1,
        freq=request.frequency,
    )[1:]


def _forecast_request(**overrides):
    from ele_trading.forecasting.contracts import ForecastRequest

    values = {
        "target": "price",
        "scope_type": "market",
        "scope_id": "mengxi",
        "horizon": 3,
        "frequency": "15min",
        "issue_time": pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai"),
        "quantiles": (0.1, 0.9),
    }
    values.update(overrides)
    return ForecastRequest(**values)


def _forecast_result(**overrides):
    from ele_trading.forecasting.contracts import ForecastResult

    request = overrides.pop("request", _forecast_request())
    index = _forecast_valid_index(request)
    values = {
        "request": request,
        "point": pd.Series([300.0, 310.0, 320.0], index=index),
        "quantiles": {
            0.1: pd.Series([280.0, 290.0, 300.0], index=index),
            0.9: pd.Series([320.0, 330.0, 340.0], index=index),
        },
        "unit": "CNY/MWh",
        "model_version": "simple-price-v1",
        "feature_as_of": request.issue_time,
        "quality_flags": (),
    }
    values.update(overrides)
    return ForecastResult(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", ""),
        ("scope_type", " "),
        ("scope_id", ""),
    ],
)
def test_forecast_request_rejects_empty_identity_fields(field: str, value: str):
    """Removing identity validation must allow an untraceable forecast request."""
    with pytest.raises(ValueError, match=field):
        _forecast_request(**{field: value})


@pytest.mark.parametrize(
    "quantiles",
    [
        (0.1, 0.1),
        (0.9, 0.1),
        (0.0, 0.9),
        (0.1, 1.0),
    ],
)
def test_forecast_request_rejects_invalid_quantiles(quantiles: tuple[float, ...]):
    """Duplicate, unordered, or boundary quantiles must not enter a request."""
    with pytest.raises(ValueError, match="quantiles"):
        _forecast_request(quantiles=quantiles)


@pytest.mark.parametrize(
    "issue_time",
    [
        pd.NaT,
        pd.Timestamp("2026-07-01 00:00"),
    ],
)
def test_forecast_request_rejects_invalid_or_naive_issue_time(issue_time):
    """NaT or timezone-naive issue times must not define a forecast vintage."""
    with pytest.raises(ValueError, match="issue_time"):
        _forecast_request(issue_time=issue_time)


@pytest.mark.parametrize("frequency", ["-15min", "0min"])
def test_forecast_request_rejects_non_forward_frequency(frequency: str):
    """A forecast grid must advance strictly beyond its issue time."""
    with pytest.raises(ValueError, match="frequency"):
        _forecast_request(frequency=frequency)


def test_valid_time_index_repeats_anchored_offset_without_skipping():
    """An anchored offset starts at the next anchor and advances one anchor at a time."""
    from ele_trading.forecasting.contracts import _valid_time_index

    request = _forecast_request(
        issue_time=pd.Timestamp(
            "2026-07-15 10:30",
            tz="Asia/Shanghai",
        ),
        frequency="MS",
    )

    assert _valid_time_index(request).equals(
        pd.DatetimeIndex(
            [
                pd.Timestamp("2026-08-01 10:30", tz="Asia/Shanghai"),
                pd.Timestamp("2026-09-01 10:30", tz="Asia/Shanghai"),
                pd.Timestamp("2026-10-01 10:30", tz="Asia/Shanghai"),
            ]
        )
    )


def test_forecast_result_accepts_aligned_finite_ordered_series():
    result = _forecast_result()

    assert result.request.target == "price"
    assert result.point.tolist() == [300.0, 310.0, 320.0]
    assert tuple(result.quantiles) == (0.1, 0.9)


@pytest.mark.parametrize("failure", ["length", "index"])
def test_forecast_result_rejects_misaligned_quantiles(failure: str):
    """Changing a quantile horizon or valid-time index must invalidate the result."""
    request = _forecast_request()
    index = pd.date_range(request.issue_time, periods=3, freq=request.frequency)
    bad_index = index[:2] if failure == "length" else index + pd.Timedelta(minutes=15)
    bad_values = [280.0, 290.0] if failure == "length" else [280.0, 290.0, 300.0]

    with pytest.raises(ValueError, match="align"):
        _forecast_result(
            request=request,
            quantiles={
                0.1: pd.Series(bad_values, index=bad_index),
                0.9: pd.Series([320.0, 330.0, 340.0], index=index),
            },
        )


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, "not-numeric"])
def test_forecast_result_rejects_non_finite_or_non_numeric_points(bad_value):
    """A non-numeric or non-finite point value must never reach optimization."""
    request = _forecast_request()
    index = pd.date_range(request.issue_time, periods=3, freq=request.frequency)

    with pytest.raises(ValueError, match="finite numeric"):
        _forecast_result(
            request=request,
            point=pd.Series([300.0, bad_value, 320.0], index=index),
        )


def test_forecast_result_rejects_crossed_quantile_bands():
    """Lower quantiles exceeding upper quantiles must be rejected."""
    request = _forecast_request()
    index = _forecast_valid_index(request)

    with pytest.raises(ValueError, match="ordered"):
        _forecast_result(
            request=request,
            quantiles={
                0.1: pd.Series([280.0, 350.0, 300.0], index=index),
                0.9: pd.Series([320.0, 330.0, 340.0], index=index),
            },
        )


def test_forecast_result_rejects_future_features():
    """Moving feature_as_of beyond issue_time must expose future information."""
    request = _forecast_request()

    with pytest.raises(ValueError, match="feature_as_of"):
        _forecast_result(
            request=request,
            feature_as_of=request.issue_time + pd.Timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    "feature_as_of",
    [
        pd.NaT,
        pd.Timestamp("2026-07-01 00:00"),
    ],
)
def test_forecast_result_rejects_invalid_or_naive_feature_as_of(feature_as_of):
    """NaT or timezone-naive feature cutoffs must not bypass no-lookahead."""
    with pytest.raises(ValueError, match="feature_as_of"):
        _forecast_result(feature_as_of=feature_as_of)


@pytest.mark.parametrize(
    "mutation",
    [
        "range",
        "unordered",
        "duplicate",
        "naive",
        "wrong_interval",
    ],
)
def test_forecast_result_rejects_invalid_valid_time_index(mutation: str):
    """Point and quantile values require the exact request valid-time grid."""
    request = _forecast_request()
    index = _forecast_valid_index(request)
    if mutation == "range":
        bad_index = pd.RangeIndex(request.horizon)
    elif mutation == "unordered":
        bad_index = index[::-1]
    elif mutation == "duplicate":
        bad_index = pd.DatetimeIndex([index[0], index[0], index[2]])
    elif mutation == "naive":
        bad_index = index.tz_localize(None)
    else:
        bad_index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=30),
            periods=request.horizon,
            freq="30min",
        )

    with pytest.raises(ValueError, match="valid-time"):
        _forecast_result(
            request=request,
            point=pd.Series([300.0, 310.0, 320.0], index=bad_index),
            quantiles={
                0.1: pd.Series([280.0, 290.0, 300.0], index=bad_index),
                0.9: pd.Series([320.0, 330.0, 340.0], index=bad_index),
            },
        )


def test_simple_provider_consumes_request_and_returns_versioned_result():
    """Reverting the provider to market/horizon arguments must break the Phase 2 API."""
    from ele_trading.forecasting.price_forecast import SimplePriceForecaster
    from ele_trading.forecasting.provider import SimpleForecastProvider

    request = _forecast_request(quantiles=(0.05, 0.95))
    history = pd.Series(
        [280.0, 300.0, 320.0],
        index=pd.date_range(
            "2026-06-30 23:15",
            periods=3,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    provider = SimpleForecastProvider(
        price_forecaster=SimplePriceForecaster(),
        load_forecaster=None,
        default_history_prices=history,
    )

    result = provider.get_price_forecast(request)

    assert result.request is request
    assert result.model_version == "SimplePriceForecaster"
    assert result.feature_as_of == history.index[-1]
    assert result.point.index.equals(_forecast_valid_index(request))
    assert tuple(result.quantiles) == request.quantiles


# 下层包（forecasting/data_provider）不得 import 的上层决策/规则/编排包
UPPER_LAYER_PREFIXES = (
    "ele_trading.trading",
    "ele_trading.positions",
    "ele_trading.operations",
    "ele_trading.backtest",
    "ele_trading.markets",
    "ele_trading.demand_response",
)


def _is_upper_layer(module: str) -> bool:
    return any(
        module == prefix or module.startswith(prefix + ".")
        for prefix in UPPER_LAYER_PREFIXES
    )


def _forecasting_trading_imports(path: Path, source: str) -> list[str]:
    """Resolve and return forecasting imports that reach upper layers."""
    tree = ast.parse(source, filename=str(path))
    relative_path = path.relative_to(SOURCE_ROOT)
    package = ".".join(
        ("ele_trading", *relative_path.parent.parts)
    )
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                module = resolve_name(
                    "." * node.level + (node.module or ""),
                    package,
                )
            else:
                module = node.module
            if module:
                modules.append(module)
                if not _is_upper_layer(module):
                    modules.extend(
                        f"{module}.{alias.name}"
                        for alias in node.names
                        if alias.name != "*"
                    )
    return [module for module in modules if _is_upper_layer(module)]


def test_forecasting_package_has_no_import_path_to_trading():
    """Adding a forecasting-to-upper-layer import in any module must fail structurally."""
    violations: list[str] = []
    for path in (SOURCE_ROOT / "forecasting").rglob("*.py"):
        if _forecasting_trading_imports(
            path,
            path.read_text(encoding="utf-8"),
        ):
            violations.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert violations == []


def test_forecasting_dependency_guard_resolves_relative_imports():
    """A relative import reaching trading must be reported like an absolute one."""
    probe = SOURCE_ROOT / "forecasting" / "probe.py"

    assert _forecasting_trading_imports(
        probe,
        "from ..trading import contracts\n",
    ) == ["ele_trading.trading"]


def test_forecasting_dependency_guard_resolves_imported_trading_alias():
    """Importing trading as a package member must not bypass the dependency guard."""
    probe = SOURCE_ROOT / "forecasting" / "probe.py"

    assert _forecasting_trading_imports(
        probe,
        "from ele_trading import trading\n",
    ) == ["ele_trading.trading"]


def test_data_provider_package_has_no_import_path_to_trading():
    """data_provider is a lower layer; it must never import trading (v2 §3.1).

    The guard helper is package-agnostic: it derives the importing package
    from the file path, so it applies unchanged to data_provider.
    """
    violations: list[str] = []
    for path in (SOURCE_ROOT / "data_provider").rglob("*.py"):
        if "todo" in path.relative_to(SOURCE_ROOT).parts:
            continue
        if _forecasting_trading_imports(
            path,
            path.read_text(encoding="utf-8"),
        ):
            violations.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert violations == []


def _market_frame(*, future_observation: bool = False) -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-07-01 00:00",
        periods=3,
        freq="15min",
        tz="Asia/Shanghai",
    )
    if future_observation:
        timestamps = timestamps + pd.Timedelta(hours=1)
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "price": [300.0, 310.0, 320.0],
            "is_observation": True,
        }
    )


def _market_snapshot(frame: pd.DataFrame, **overrides):
    from ele_trading.data_provider.contracts import MarketDataSnapshot

    values = {
        "market": "mengxi",
        "scope_type": "market",
        "scope_id": "mengxi",
        "as_of": pd.Timestamp("2026-07-01 00:30", tz="Asia/Shanghai"),
        "frame": frame,
        "version": "market-data-v1",
        "quality_flags": (),
    }
    values.update(overrides)
    return MarketDataSnapshot(**values)


def test_market_data_snapshot_accepts_traceable_observations():
    snapshot = _market_snapshot(_market_frame())

    assert snapshot.version == "market-data-v1"
    assert snapshot.frame["timestamp"].dt.tz is not None


def test_market_data_snapshot_requires_timezone_aware_as_of():
    with pytest.raises(ValueError, match="as_of.*timezone"):
        _market_snapshot(
            _market_frame(),
            as_of=pd.Timestamp("2026-07-01 00:30"),
        )


def test_market_data_snapshot_requires_timezone_aware_timestamps():
    frame = _market_frame()
    frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)

    with pytest.raises(ValueError, match="timestamp.*timezone"):
        _market_snapshot(frame)


@pytest.mark.parametrize("mutation", ["unordered", "duplicate"])
def test_market_data_snapshot_rejects_unordered_or_duplicate_timestamps(mutation: str):
    """Sorting or uniqueness validation removal must admit ambiguous market rows."""
    frame = _market_frame()
    if mutation == "unordered":
        frame = frame.iloc[[1, 0, 2]].reset_index(drop=True)
    else:
        frame.loc[1, "timestamp"] = frame.loc[0, "timestamp"]

    with pytest.raises(ValueError, match=mutation):
        _market_snapshot(frame)


def test_market_data_snapshot_rejects_observations_after_as_of():
    with pytest.raises(ValueError, match="newer than as_of"):
        _market_snapshot(_market_frame(future_observation=True))


def test_market_data_snapshot_allows_future_non_observation_rows():
    frame = _market_frame(future_observation=True)
    frame["is_observation"] = False

    snapshot = _market_snapshot(frame)

    assert len(snapshot.frame) == 3


@pytest.mark.parametrize("invalid_value", [None, "false", 0])
def test_market_data_snapshot_rejects_non_boolean_observation_flags(
    invalid_value,
):
    """Only a real boolean False may exempt a future row from the as_of cutoff."""
    frame = _market_frame(future_observation=True)
    frame["is_observation"] = [False, invalid_value, False]

    with pytest.raises(ValueError, match="is_observation"):
        _market_snapshot(frame)


def test_market_data_snapshot_requires_observation_flag_column():
    """Omitting observation provenance must not silently classify future rows."""
    frame = _market_frame().drop(columns="is_observation")

    with pytest.raises(ValueError, match="is_observation"):
        _market_snapshot(frame)


def test_trading_dataset_builds_market_snapshot_without_archived_dependency():
    from ele_trading.data_provider.contracts import MarketDataSnapshot
    from ele_trading.data_provider.market_data import build_trading_case_dataset

    index = pd.date_range(
        "2026-07-01 00:00",
        periods=3,
        freq="15min",
        tz="Asia/Shanghai",
    )
    load_df = pd.DataFrame(
        {
            "timestamp": index,
            "load_kw": [100.0, 120.0, 90.0],
            "quality_score": [1.0, 0.9, 1.0],
        }
    )
    pv_series = pd.Series([10.0, 20.0, 0.0], index=index)
    wind_series = pd.Series([5.0, 5.0, 5.0], index=index)
    prices = pd.DataFrame(
        {
            "timestamp": index,
            "buy_price": [0.5, 0.6, 0.4],
            "sell_price": [0.2, 0.2, 0.2],
        }
    )

    snapshot = build_trading_case_dataset(
        load_df,
        pv_series,
        wind_series,
        prices,
        market="mengxi",
        scope_type="portfolio",
        scope_id="demo",
        as_of=index[-1],
        version="fixture-v1",
    )

    assert isinstance(snapshot, MarketDataSnapshot)
    assert snapshot.version == "fixture-v1"
    assert list(snapshot.frame.columns) == [
        "timestamp",
        "load_forecast_kw",
        "pv_forecast_kw",
        "wind_forecast_kw",
        "price_forecast",
        "scenario_id",
        "availability_flag",
        "quality_score",
        "is_observation",
    ]

    market_data_path = SOURCE_ROOT / "data_provider" / "market_data.py"
    tree = ast.parse(
        market_data_path.read_text(encoding="utf-8"),
        filename=str(market_data_path),
    )
    imports = [
        module
        for node in ast.walk(tree)
        for module in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module]
            if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
    ]
    assert not any("todo" in module or "investment" in module for module in imports)


def test_market_csv_loader_returns_versioned_snapshot(tmp_path: Path):
    """Returning a bare DataFrame must lose the as_of and version lineage."""
    from ele_trading.data_provider.market_data import load_market_data_csv

    source = tmp_path / "market.csv"
    _market_frame().to_csv(source, index=False)

    snapshot = load_market_data_csv(
        source,
        market="mengxi",
        scope_type="market",
        scope_id="mengxi",
        as_of=pd.Timestamp("2026-07-01 00:30", tz="Asia/Shanghai"),
        version="fixture-v1",
    )

    assert snapshot.version == "fixture-v1"
    assert snapshot.as_of == pd.Timestamp(
        "2026-07-01 00:30",
        tz="Asia/Shanghai",
    )
    assert snapshot.frame["price"].tolist() == [300.0, 310.0, 320.0]


def test_observed_power_csv_uses_clear_active_contract(tmp_path: Path):
    """Observed load/renewable input must not reuse investment profile types."""
    from ele_trading.data_provider.market_data import (
        load_observed_power_series,
    )
    from ele_trading.data_provider.schemas import ObservedPowerSeries

    source = tmp_path / "observed-load.csv"
    pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-07-01 00:00",
                periods=3,
                freq="15min",
                tz="Asia/Shanghai",
            ),
            "load_kw": [100.0, 110.0, 105.0],
        }
    ).to_csv(source, index=False)

    observed = load_observed_power_series(
        source,
        value_col="load_kw",
        unit="kW",
    )

    assert isinstance(observed, ObservedPowerSeries)
    assert observed.values.name == "load_kw"
    assert observed.values.tolist() == [100.0, 110.0, 105.0]
    assert observed.unit == "kW"
    assert observed.source == str(source)


def test_active_data_provider_has_no_investment_profile_api():
    """Investment profile types and builders must only be reachable under todo."""
    import ele_trading.data_provider as data_provider
    from ele_trading.data_provider import schemas

    forbidden_names = {
        "LoadProfileBuildConfig",
        "LoadProfileResult",
        "PVProfileConfig",
        "WindProfileConfig",
        "RenewableProfileResult",
        "build_daily_energy_targets",
        "build_load_profile",
        "build_load_profile_from_raw",
        "fill_missing_days_by_reference",
        "fill_missing_load_by_daily_energy",
        "load_load_profile",
        "load_load_profile_build_config",
        "load_pv_profile_config",
        "load_renewable_profile",
        "load_wind_profile_config",
        "read_load_excel_folder",
        "save_load_profile",
        "shift_history_profile",
        "smooth_history_shape",
    }

    assert not (
        forbidden_names
        & (
            set(vars(data_provider))
            | set(vars(schemas))
        )
    )
    assert not (SOURCE_ROOT / "data_provider" / "load_profile.py").exists()


def test_active_data_provider_exposes_phase2_authority_modules():
    from ele_trading.data_provider import (
        MarketDataSnapshot,
        asset_data,
        market_data,
        quality,
        weather_data,
    )

    assert MarketDataSnapshot is not None
    assert market_data.__name__.endswith(".market_data")
    assert weather_data.__name__.endswith(".weather_data")
    assert asset_data.__name__.endswith(".asset_data")
    assert quality.__name__.endswith(".quality")


def test_active_data_provider_sources_do_not_import_todo():
    violations: list[str] = []
    data_provider_root = SOURCE_ROOT / "data_provider"
    for path in data_provider_root.rglob("*.py"):
        if "todo" in path.relative_to(data_provider_root).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            if any(module == "todo" or ".todo" in module for module in modules):
                violations.append(path.relative_to(PROJECT_ROOT).as_posix())

    assert violations == []
