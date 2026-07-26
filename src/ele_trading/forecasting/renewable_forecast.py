from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from .base import ForecastOutput
from .contracts import (
    ForecastRequest,
    ForecastResult,
    _prepare_history_series,
    _valid_time_index,
)


_PHYSICAL_WEATHER_SCHEMA = {
    "wind_power": ("wind_speed", "m/s"),
    "pv_power": ("irradiance", "W/m2"),
}


def wind_power_curve(
    wind_speed: np.ndarray,
    capacity_mw: float,
    *,
    cut_in: float = 3.0,
    rated: float = 12.0,
    cut_out: float = 25.0,
) -> np.ndarray:
    """Convert wind speed to bounded MW using a transparent turbine curve."""
    speed = np.asarray(wind_speed, dtype=float)
    output = np.zeros_like(speed)
    ramp = (speed > cut_in) & (speed < rated)
    output[ramp] = capacity_mw * (
        (speed[ramp] ** 3 - cut_in**3)
        / (rated**3 - cut_in**3)
    )
    output[(speed >= rated) & (speed < cut_out)] = capacity_mw
    return np.clip(output, 0.0, capacity_mw)


def pv_physical_output(
    irradiance: np.ndarray,
    valid_times: pd.DatetimeIndex,
    capacity_mw: float,
    *,
    site_timezone: str,
) -> np.ndarray:
    """Convert plane irradiance to bounded MW and enforce local night zero."""
    irradiance_values = np.asarray(irradiance, dtype=float)
    output = capacity_mw * np.clip(
        irradiance_values / 1000.0,
        0.0,
        1.0,
    )
    local_times = valid_times.tz_convert(
        _site_zone(site_timezone)
    )
    daylight = (local_times.hour >= 6) & (local_times.hour < 18)
    return np.where(daylight, output, 0.0)


def calibrate_equivalent_hours(
    power_mw: np.ndarray,
    capacity_mw: float,
    equiv_hours: float,
) -> np.ndarray:
    """Scale a sample profile to its requested equivalent-full-load level."""
    if (
        not np.isfinite(equiv_hours)
        or equiv_hours <= 0.0
        or equiv_hours > 8760.0
    ):
        raise ValueError("equiv_hours must be within (0, 8760]")
    values = np.asarray(power_mw, dtype=float)
    current_mean = float(np.mean(values))
    if current_mean <= 0.0:
        return values
    target_mean = capacity_mw * equiv_hours / 8760.0
    return np.clip(
        values * target_mean / current_mean,
        0.0,
        capacity_mw,
    )


def _site_zone(site_timezone: object) -> ZoneInfo:
    if not isinstance(site_timezone, str) or not site_timezone.strip():
        raise ValueError("site_timezone must be an explicit IANA timezone")
    try:
        return ZoneInfo(site_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"site_timezone is not recognized: {site_timezone!r}"
        ) from exc


@dataclass(frozen=True, slots=True)
class RenewableForecastVintage:
    """One externally issued renewable forecast."""

    issue_time: pd.Timestamp
    values: pd.Series


class ExternalRenewableForecastAdapter(Protocol):
    """Boundary implemented by an external renewable forecast service."""

    def get_forecast(
        self,
        request: ForecastRequest,
    ) -> RenewableForecastVintage | None:
        """Return a forecast available by the request issue time."""
        ...


class RenewableForecastModel(Protocol):
    """Common interface for physical, statistical, and external paths."""

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Return a contract-aligned renewable power forecast in MW."""
        ...


class RenewablePowerForecastModel:
    """Common request-oriented wind/PV forecast implementation."""

    def __init__(
        self,
        *,
        target: str,
        mode: str,
        capacity_by_scope: Mapping[tuple[str, str], float],
        history_by_scope: Mapping[tuple[str, str], pd.Series] | None = None,
        members_by_scope: Mapping[
            tuple[str, str],
            tuple[str, ...],
        ] | None = None,
        adapter: ExternalRenewableForecastAdapter | None = None,
    ) -> None:
        if target not in {"wind_power", "pv_power"}:
            raise ValueError("renewable target must be wind_power or pv_power")
        if mode not in {"physical", "statistical", "external"}:
            raise ValueError(
                "renewable mode must be physical, statistical, or external"
            )
        if mode == "external" and adapter is None:
            raise ValueError("external renewable mode requires an adapter")
        self.target = target
        self.mode = mode
        self.capacity_by_scope = {
            key: float(value)
            for key, value in capacity_by_scope.items()
        }
        self.history_by_scope = dict(history_by_scope or {})
        self.members_by_scope = dict(members_by_scope or {})
        self.adapter = adapter

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if request.target != self.target:
            raise ValueError(
                f"renewable model target is {self.target!r}, "
                f"got {request.target!r}"
            )
        valid_times = _valid_time_index(request)
        aggregate_key = (request.scope_type, request.scope_id)
        members = self.members_by_scope.get(aggregate_key)
        if members:
            if self.mode == "external":
                raise ValueError(
                    "external portfolio forecasts require an aggregate adapter"
                )
            site_data = request.data.get("site_data")
            if not isinstance(site_data, Mapping):
                raise ValueError(
                    "aggregate renewable request data must include site_data"
                )
            values = np.zeros(request.horizon, dtype=float)
            feature_times: list[pd.Timestamp] = []
            flags: list[str] = []
            for site_id in members:
                data = site_data.get(site_id)
                if not isinstance(data, Mapping):
                    raise ValueError(
                        f"renewable site_data is missing {site_id!r}"
                    )
                site_values, feature_as_of, site_flags = self._site_forecast(
                    site_id,
                    data,
                    request,
                    valid_times,
                )
                values += site_values
                feature_times.append(feature_as_of)
                flags.extend(site_flags)
            capacity = sum(
                self._capacity(("site", site_id))
                for site_id in members
            )
            values = np.clip(values, 0.0, capacity)
            feature_as_of = max(feature_times)
            flags.append("aggregate:bottom_up")
        else:
            capacity = self._capacity(aggregate_key)
            if self.mode == "external":
                values, feature_as_of, flags = self._external_forecast(
                    request,
                    valid_times,
                    capacity,
                )
            else:
                values, feature_as_of, flags = self._site_forecast(
                    request.scope_id,
                    request.data,
                    request,
                    valid_times,
                    scope_type=request.scope_type,
                )

        point = pd.Series(values, index=valid_times, dtype=float)
        quantiles = {
            level: point.copy()
            for level in request.quantiles
        }
        return ForecastResult(
            request=request,
            point=point,
            quantiles=quantiles,
            unit="MW",
            model_version=f"{self.target}-{self.mode}-v1",
            feature_as_of=feature_as_of,
            quality_flags=tuple(dict.fromkeys(flags)),
        )

    def _site_forecast(
        self,
        site_id: str,
        data: Mapping[str, object],
        request: ForecastRequest,
        valid_times: pd.DatetimeIndex,
        *,
        scope_type: str = "site",
    ) -> tuple[np.ndarray, pd.Timestamp, list[str]]:
        key = (scope_type, site_id)
        capacity = self._capacity(key)
        availability = self._availability(
            data.get("availability", 1.0),
            request.horizon,
        )
        site_timezone = (
            self._site_timezone(data)
            if self.target == "pv_power"
            else None
        )
        if self.mode == "physical":
            weather_values, feature_as_of, source_flag = (
                self._physical_weather(
                    data,
                    request,
                    valid_times,
                    expected_scope_type=scope_type,
                    expected_scope_id=site_id,
                )
            )
            if self.target == "wind_power":
                raw = wind_power_curve(weather_values, capacity)
            else:
                raw = pv_physical_output(
                    weather_values,
                    valid_times,
                    capacity,
                    site_timezone=site_timezone,
                )
            flags = ["path:physical", source_flag]
        else:
            history = self.history_by_scope.get(key)
            if history is None:
                raise ValueError(
                    f"renewable history is missing for scope {key!r}"
                )
            eligible = _prepare_history_series(
                history,
                issue_time=request.issue_time,
                frequency=request.frequency,
                field_name="renewable history",
            )
            raw = np.full(
                request.horizon,
                float(eligible.iloc[-1]),
            )
            flags = [
                "baseline:statistical-persistence",
                "source:historical_output",
            ]
            feature_as_of = eligible.index.max()
        bounded = np.clip(raw, 0.0, capacity) * availability
        if self.target == "pv_power":
            local_times = valid_times.tz_convert(
                _site_zone(site_timezone)
            )
            daylight = (local_times.hour >= 6) & (local_times.hour < 18)
            bounded = np.where(daylight, bounded, 0.0)
        return bounded, feature_as_of, flags

    def _external_forecast(
        self,
        request: ForecastRequest,
        valid_times: pd.DatetimeIndex,
        capacity: float,
    ) -> tuple[np.ndarray, pd.Timestamp, list[str]]:
        vintage = self.adapter.get_forecast(request)
        if vintage is None:
            raise ValueError("external renewable forecast is unavailable")
        issue_time = pd.Timestamp(vintage.issue_time)
        if issue_time.tzinfo is None or issue_time > request.issue_time:
            raise ValueError(
                "external renewable forecast issue_time is not eligible"
            )
        values = vintage.values.reindex(valid_times)
        if values.isna().any():
            raise ValueError(
                "external renewable forecast does not cover valid times"
            )
        availability = self._availability(
            request.data.get("availability", 1.0),
            request.horizon,
        )
        bounded = np.clip(
            values.to_numpy(dtype=float),
            0.0,
            capacity,
        ) * availability
        if self.target == "pv_power":
            local_times = valid_times.tz_convert(
                _site_zone(
                    request.data.get("site_timezone")
                )
            )
            daylight = (local_times.hour >= 6) & (local_times.hour < 18)
            bounded = np.where(daylight, bounded, 0.0)
        return bounded, issue_time, ["source:external"]

    def _physical_weather(
        self,
        data: Mapping[str, object],
        request: ForecastRequest,
        valid_times: pd.DatetimeIndex,
        *,
        expected_scope_type: str,
        expected_scope_id: str,
    ) -> tuple[np.ndarray, pd.Timestamp, str]:
        expected_variable, expected_unit = _PHYSICAL_WEATHER_SCHEMA[
            self.target
        ]
        if data.get("weather_variable") != expected_variable:
            raise ValueError(
                f"{self.target} physical weather_variable must be "
                f"{expected_variable!r}"
            )

        weather_forecast = data.get("weather_forecast")
        if weather_forecast is not None:
            if not isinstance(weather_forecast, ForecastResult):
                raise ValueError(
                    "weather_forecast must be a "
                    "forecasting.contracts.ForecastResult"
                )
            if weather_forecast.request.target != "weather":
                raise ValueError(
                    "weather ForecastResult must have target 'weather'"
                )
            if (
                weather_forecast.request.scope_type
                != expected_scope_type
                or weather_forecast.request.scope_id != expected_scope_id
            ):
                raise ValueError(
                    "weather ForecastResult scope must match renewable scope"
                )
            if weather_forecast.request.issue_time > request.issue_time:
                raise ValueError(
                    "weather forecast issue_time must not be later "
                    "than renewable request issue_time"
                )
            if (
                weather_forecast.request.data.get("weather_variable")
                != expected_variable
            ):
                raise ValueError(
                    "weather ForecastResult weather_variable must be "
                    f"{expected_variable!r}"
                )
            if weather_forecast.unit != expected_unit:
                raise ValueError(
                    "weather ForecastResult unit must be "
                    f"{expected_unit!r}"
                )
            values = weather_forecast.point
            feature_as_of = weather_forecast.feature_as_of
            source_flag = "source:weather_forecast"
        else:
            if data.get("weather_unit") != expected_unit:
                raise ValueError(
                    f"{self.target} physical weather_unit must be "
                    f"{expected_unit!r}"
                )
            values = data.get(expected_variable)
            if not isinstance(values, pd.Series):
                raise ValueError(
                    f"{expected_variable} must be an indexed weather Series "
                    "with explicit feature_as_of"
                )
            feature_as_of = data.get("feature_as_of")
            source_flag = "source:explicit_weather"

        if (
            not isinstance(values, pd.Series)
            or not isinstance(values.index, pd.DatetimeIndex)
            or values.index.tz is None
            or not values.index.equals(valid_times)
        ):
            raise ValueError(
                "physical weather values must align with the request "
                "valid-time index"
            )
        timestamp = pd.Timestamp(feature_as_of)
        if (
            pd.isna(timestamp)
            or timestamp.tzinfo is None
            or timestamp > request.issue_time
        ):
            raise ValueError(
                "physical weather feature_as_of must be timezone-aware "
                "and not later than request.issue_time"
            )
        array = values.to_numpy(dtype=float)
        if not np.isfinite(array).all():
            raise ValueError(
                "physical weather values must contain finite values"
            )
        return array, timestamp, source_flag

    def _capacity(self, key: tuple[str, str]) -> float:
        capacity = self.capacity_by_scope.get(key)
        if capacity is None or capacity <= 0.0:
            raise ValueError(
                f"positive renewable capacity is required for scope {key!r}"
            )
        return capacity

    @staticmethod
    def _availability(
        value: object,
        horizon: int,
    ) -> np.ndarray:
        if np.isscalar(value):
            array = np.full(horizon, float(value))
        else:
            array = np.asarray(value, dtype=float)
        if (
            array.ndim != 1
            or len(array) != horizon
            or not np.isfinite(array).all()
            or (array < 0.0).any()
            or (array > 1.0).any()
        ):
            raise ValueError(
                "availability must align with horizon and stay within [0, 1]"
            )
        return array

    @staticmethod
    def _site_timezone(data: Mapping[str, object]) -> str:
        value = data.get("site_timezone")
        _site_zone(value)
        return str(value)


class RenewableForecaster:
    """统一可再生出力预测包装器。

    当前版本先支持两种最基础输入：
    - 已预计算 profile 的直接截取
    - 历史序列 persistence 外推
    """

    def predict(self, history_values: list[float], horizon: int) -> ForecastOutput:
        if not history_values:
            raise ValueError("history_values 不能为空")
        last_value = float(history_values[-1])
        return ForecastOutput(horizon=horizon, point_forecast=[last_value] * horizon)

    def predict_from_profile(self, profile_values: list[float], horizon: int) -> ForecastOutput:
        if not profile_values:
            raise ValueError("profile_values 不能为空")
        if horizon <= 0:
            raise ValueError("horizon must be positive")
        point = [float(value) for value in profile_values[:horizon]]
        if len(point) < horizon:
            point.extend([point[-1]] * (horizon - len(point)))
        return ForecastOutput(horizon=horizon, point_forecast=point)


class RenewableForecastStub(RenewableForecaster):
    """兼容旧名称。"""
