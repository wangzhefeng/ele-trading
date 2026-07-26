"""Validated contracts for joint forecast scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd


_NON_NEGATIVE_TARGETS = {
    "load",
    "load_power",
    "wind",
    "wind_power",
    "pv",
    "pv_power",
    "solar",
    "solar_power",
}


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_valid_index(
    value: pd.Index,
    field_name: str,
) -> pd.DatetimeIndex:
    if not isinstance(value, pd.DatetimeIndex) or value.tz is None:
        raise ValueError(
            f"{field_name} must be a timezone-aware DatetimeIndex"
        )
    if value.empty:
        raise ValueError(f"{field_name} must not be empty")
    if value.has_duplicates:
        raise ValueError(f"{field_name} must not contain duplicates")
    if not value.is_monotonic_increasing:
        raise ValueError(f"{field_name} must be monotonic increasing")
    return value


@dataclass(slots=True)
class Scenario:
    """One traceable realization across all requested forecast targets."""

    scenario_id: str
    probability: float
    issue_time: pd.Timestamp
    trajectories: Mapping[str, pd.Series]
    seed: int
    source_versions: Mapping[str, str]

    def __post_init__(self) -> None:
        _require_non_empty(self.scenario_id, "scenario_id")
        if (
            not np.isfinite(self.probability)
            or float(self.probability) <= 0.0
        ):
            raise ValueError("probability must be finite and positive")
        self.probability = float(self.probability)

        issue_time = pd.Timestamp(self.issue_time)
        if pd.isna(issue_time) or issue_time.tzinfo is None:
            raise ValueError(
                "issue_time must be a valid timezone-aware timestamp"
            )
        self.issue_time = issue_time

        if not isinstance(self.seed, (int, np.integer)):
            raise ValueError("seed must be an integer")
        self.seed = int(self.seed)

        if not isinstance(self.trajectories, Mapping) or not self.trajectories:
            raise ValueError("trajectories must be a non-empty mapping")
        trajectories = dict(self.trajectories)
        common_index: pd.DatetimeIndex | None = None
        for target, trajectory in trajectories.items():
            _require_non_empty(target, "trajectory target")
            if not isinstance(trajectory, pd.Series):
                raise ValueError(
                    f"trajectory {target!r} must be a pandas Series"
                )
            index = _require_valid_index(
                trajectory.index,
                f"trajectory {target!r} valid-time index",
            )
            if common_index is None:
                common_index = index
            elif not index.equals(common_index):
                raise ValueError(
                    "all trajectories must use an identical valid-time index"
                )
            if not pd.api.types.is_numeric_dtype(trajectory.dtype):
                raise ValueError(
                    f"trajectory {target!r} must contain finite numeric values"
                )
            values = trajectory.to_numpy(dtype=float)
            if not np.isfinite(values).all():
                raise ValueError(
                    f"trajectory {target!r} must contain finite numeric values"
                )
            if target.lower() in _NON_NEGATIVE_TARGETS and (values < 0.0).any():
                raise ValueError(
                    f"trajectory {target!r} must contain non-negative values"
                )
        assert common_index is not None
        if self.issue_time >= common_index[0]:
            raise ValueError(
                "issue_time must be earlier than the first valid time"
            )
        self.trajectories = trajectories

        if not isinstance(self.source_versions, Mapping):
            raise ValueError("source_versions must be a mapping")
        source_versions = dict(self.source_versions)
        if set(source_versions) != set(trajectories):
            raise ValueError(
                "source_versions must trace every trajectory target"
            )
        for target, version in source_versions.items():
            _require_non_empty(
                version,
                f"source_versions[{target!r}]",
            )
        self.source_versions = source_versions

    @property
    def valid_time_index(self) -> pd.DatetimeIndex:
        return next(iter(self.trajectories.values())).index

    @property
    def horizon(self) -> int:
        return len(self.valid_time_index)


@dataclass(slots=True)
class ScenarioSet:
    """Joint scenarios sharing one horizon, index, unit map and vintage."""

    horizon: int
    valid_time_index: pd.DatetimeIndex
    units: Mapping[str, str]
    scenarios: tuple[Scenario, ...]
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, int) or self.horizon <= 0:
            raise ValueError("horizon must be a positive integer")
        index = _require_valid_index(
            self.valid_time_index,
            "valid_time_index",
        )
        if len(index) != self.horizon:
            raise ValueError(
                "valid_time_index length must match horizon"
            )
        self.valid_time_index = index.copy()

        if not isinstance(self.units, Mapping) or not self.units:
            raise ValueError("units must be a non-empty mapping")
        units = dict(self.units)
        for target, unit in units.items():
            _require_non_empty(target, "unit target")
            _require_non_empty(unit, f"units[{target!r}]")
        self.units = units

        scenarios = tuple(self.scenarios)
        if not scenarios:
            raise ValueError("scenarios must not be empty")
        if not all(isinstance(item, Scenario) for item in scenarios):
            raise ValueError("scenarios must contain Scenario objects")
        ids = [item.scenario_id for item in scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario IDs must be unique")
        issue_time = scenarios[0].issue_time
        source_versions = dict(scenarios[0].source_versions)
        for item in scenarios:
            if item.horizon != self.horizon:
                raise ValueError(
                    "scenario horizon must match ScenarioSet horizon"
                )
            if not item.valid_time_index.equals(self.valid_time_index):
                raise ValueError(
                    "scenario valid-time index must match ScenarioSet "
                    "valid-time index"
                )
            if item.issue_time != issue_time:
                raise ValueError(
                    "all scenarios must use the same issue_time"
                )
            if set(item.trajectories) != set(units):
                raise ValueError(
                    "scenario trajectory targets must match units"
                )
            if dict(item.source_versions) != source_versions:
                raise ValueError(
                    "all scenarios must use consistent source_versions"
                )
        probability_sum = float(
            sum(item.probability for item in scenarios)
        )
        if not np.isclose(
            probability_sum,
            1.0,
            rtol=0.0,
            atol=1e-9,
        ):
            raise ValueError("scenario probabilities must sum to 1")
        self.scenarios = scenarios

        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        self.metadata = dict(self.metadata)

    @property
    def issue_time(self) -> pd.Timestamp:
        return self.scenarios[0].issue_time

    @property
    def source_versions(self) -> dict[str, str]:
        """Return the common forecast versions used to build this set."""
        first = dict(self.scenarios[0].source_versions)
        if any(
            item.source_versions != first
            for item in self.scenarios[1:]
        ):
            raise ValueError(
                "scenarios do not share common source versions"
            )
        return first
