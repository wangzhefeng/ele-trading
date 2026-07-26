"""Weather forecast adapters and deterministic baselines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import pandas as pd

from .contracts import (
    ForecastRequest,
    ForecastResult,
    _prepare_history_series,
    _valid_time_index,
)


@dataclass(frozen=True, slots=True)
class WeatherForecastVintage:
    """One externally issued weather forecast vintage."""

    issue_time: pd.Timestamp
    values: pd.Series
    unit: str


class ExternalWeatherForecastAdapter(Protocol):
    """Boundary implemented by an external weather forecast provider."""

    def get_forecast(
        self,
        request: ForecastRequest,
    ) -> WeatherForecastVintage | None:
        """Return the latest eligible vintage, or ``None`` when unavailable."""
        ...


class ArchivedWeatherForecastAdapter:
    """In-memory archive used to select no-lookahead forecast vintages."""

    def __init__(self) -> None:
        self._vintages: dict[
            tuple[str, str],
            list[WeatherForecastVintage],
        ] = {}

    def add(
        self,
        *,
        scope_type: str,
        scope_id: str,
        issue_time: pd.Timestamp,
        values: pd.Series,
        unit: str,
    ) -> None:
        timestamp = pd.Timestamp(issue_time)
        if timestamp.tzinfo is None:
            raise ValueError("weather vintage issue_time must be timezone-aware")
        if not isinstance(values.index, pd.DatetimeIndex) or values.index.tz is None:
            raise ValueError("weather vintage values must use a timezone-aware index")
        key = (scope_type, scope_id)
        self._vintages.setdefault(key, []).append(
            WeatherForecastVintage(timestamp, values.copy(), unit)
        )
        self._vintages[key].sort(key=lambda vintage: vintage.issue_time)

    def get_forecast(
        self,
        request: ForecastRequest,
    ) -> WeatherForecastVintage | None:
        candidates = [
            vintage
            for vintage in self._vintages.get(
                (request.scope_type, request.scope_id),
                (),
            )
            if vintage.issue_time <= request.issue_time
        ]
        return candidates[-1] if candidates else None


class WeatherBaselineModel:
    """Archived-vintage weather model with persistence/climatology fallback."""

    def __init__(
        self,
        *,
        archive: ExternalWeatherForecastAdapter | None = None,
        history_by_scope: Mapping[tuple[str, str], pd.Series] | None = None,
        baseline: str = "persistence",
        bias_correction: float = 0.0,
        unit_by_scope: Mapping[tuple[str, str], str] | None = None,
        model_version: str = "weather-baseline-v1",
    ) -> None:
        if baseline not in {"persistence", "climatology"}:
            raise ValueError("weather baseline must be persistence or climatology")
        self.archive = archive
        self.history_by_scope = dict(history_by_scope or {})
        self.baseline = baseline
        self.bias_correction = float(bias_correction)
        self.unit_by_scope = dict(unit_by_scope or {})
        self.model_version = model_version

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        if request.target != "weather":
            raise ValueError("weather model requires target 'weather'")
        valid_times = _valid_time_index(request)
        flags: list[str] = []

        vintage = (
            self.archive.get_forecast(request)
            if self.archive is not None
            else None
        )
        if vintage is not None:
            point = vintage.values.reindex(valid_times)
            if point.isna().any():
                raise ValueError(
                    "archived weather vintage does not cover every requested valid time"
                )
            feature_as_of = vintage.issue_time
            unit = vintage.unit
            flags.append("source:archived")
        else:
            key = (request.scope_type, request.scope_id)
            history = self.history_by_scope.get(key)
            if history is None:
                raise ValueError("weather history or archived source is required")
            eligible = _prepare_history_series(
                history,
                issue_time=request.issue_time,
                frequency=request.frequency,
                field_name="weather history",
            )
            feature_as_of = eligible.index.max()
            unit = self.unit_by_scope.get(key)
            if unit is None:
                raise ValueError("weather unit is required for baseline forecasts")
            if self.baseline == "persistence":
                point = pd.Series(
                    float(eligible.iloc[-1]),
                    index=valid_times,
                    dtype=float,
                )
            else:
                slot_means = eligible.groupby(
                    [eligible.index.hour, eligible.index.minute]
                ).mean()
                values: list[float] = []
                for valid_time in valid_times:
                    slot = (valid_time.hour, valid_time.minute)
                    if slot not in slot_means.index:
                        raise ValueError(
                            "weather climatology lacks a requested time slot"
                        )
                    values.append(float(slot_means.loc[slot]))
                point = pd.Series(values, index=valid_times, dtype=float)
            flags.append(f"baseline:{self.baseline}")

        if self.bias_correction:
            point = point.astype(float) + self.bias_correction
            flags.append("bias_corrected")
        else:
            point = point.astype(float)
        quantiles = {
            level: point.copy()
            for level in request.quantiles
        }
        return ForecastResult(
            request=request,
            point=point,
            quantiles=quantiles,
            unit=unit,
            model_version=self.model_version,
            feature_as_of=feature_as_of,
            quality_flags=tuple(flags),
        )
