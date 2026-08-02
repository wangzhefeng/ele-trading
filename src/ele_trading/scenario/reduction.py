"""Backward Kantorovich/Wasserstein L1 scenario reduction."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist

from .contracts import Scenario, ScenarioSet


@dataclass(frozen=True, slots=True)
class ReductionDiagnostics:
    """Distribution and event-retention evidence for one reduction."""

    original_count: int
    retained_count: int
    wasserstein_l1: float
    probability_transfers: dict[str, str]
    mean_drift: dict[str, float]
    quantile_drift: dict[str, float]
    critical_peak_scenario_id: str
    critical_ramp_scenario_id: str
    critical_events_retained: bool


def _joint_vectors(scenario_set: ScenarioSet) -> np.ndarray:
    """Return unit-normalized joint trajectories for L1 comparison."""
    target_blocks: list[np.ndarray] = []
    for target in scenario_set.units:
        matrix = np.vstack(
            [
                item.trajectories[target].to_numpy(dtype=float)
                for item in scenario_set.scenarios
            ]
        )
        value_range = float(matrix.max() - matrix.min())
        scale = value_range if value_range > 1e-12 else 1.0
        target_blocks.append(matrix / scale)
    return np.hstack(target_blocks)


def _critical_event_indices(
    scenario_set: ScenarioSet,
) -> tuple[int, int]:
    targets = set(scenario_set.units)
    load_target = next(
        (
            target
            for target in ("load", "load_power")
            if target in targets
        ),
        None,
    )
    if load_target is not None:
        event_matrix = np.vstack(
            [
                item.trajectories[load_target].to_numpy(dtype=float)
                for item in scenario_set.scenarios
            ]
        )
        for renewable_target in (
            "wind",
            "wind_power",
            "pv",
            "pv_power",
            "solar",
            "solar_power",
        ):
            if renewable_target in targets:
                event_matrix -= np.vstack(
                    [
                        item.trajectories[
                            renewable_target
                        ].to_numpy(dtype=float)
                        for item in scenario_set.scenarios
                    ]
                )
    else:
        event_target = (
            "price"
            if "price" in targets
            else next(iter(scenario_set.units))
        )
        event_matrix = np.vstack(
            [
                item.trajectories[event_target].to_numpy(dtype=float)
                for item in scenario_set.scenarios
            ]
        )

    peak_scores = event_matrix.max(axis=1)
    peak_index = int(np.argmax(peak_scores))
    if scenario_set.horizon < 2:
        return peak_index, peak_index
    ramp_scores = np.abs(np.diff(event_matrix, axis=1)).max(axis=1)
    if float(ramp_scores.max()) <= 1e-12:
        return peak_index, peak_index
    return peak_index, int(np.argmax(ramp_scores))


def _weighted_quantile(
    values: np.ndarray,
    weights: np.ndarray,
    quantile: float,
) -> float:
    order = np.argsort(values, kind="stable")
    ordered_values = values[order]
    ordered_weights = weights[order]
    cumulative = np.cumsum(ordered_weights)
    index = int(np.searchsorted(cumulative, quantile, side="left"))
    return float(ordered_values[min(index, len(values) - 1)])


def _distribution_drift(
    original: ScenarioSet,
    reduced: ScenarioSet,
    quantiles: tuple[float, ...],
) -> tuple[dict[str, float], dict[str, float]]:
    original_weights = np.asarray(
        [item.probability for item in original.scenarios],
        dtype=float,
    )
    reduced_weights = np.asarray(
        [item.probability for item in reduced.scenarios],
        dtype=float,
    )
    mean_drift: dict[str, float] = {}
    quantile_drift: dict[str, float] = {}
    for target in original.units:
        original_values = np.vstack(
            [
                item.trajectories[target].to_numpy(dtype=float)
                for item in original.scenarios
            ]
        )
        reduced_values = np.vstack(
            [
                item.trajectories[target].to_numpy(dtype=float)
                for item in reduced.scenarios
            ]
        )
        original_mean = original_weights @ original_values
        reduced_mean = reduced_weights @ reduced_values
        mean_drift[target] = float(
            np.max(np.abs(original_mean - reduced_mean))
        )

        largest_quantile_drift = 0.0
        for time_index in range(original.horizon):
            for quantile in quantiles:
                before = _weighted_quantile(
                    original_values[:, time_index],
                    original_weights,
                    quantile,
                )
                after = _weighted_quantile(
                    reduced_values[:, time_index],
                    reduced_weights,
                    quantile,
                )
                largest_quantile_drift = max(
                    largest_quantile_drift,
                    abs(before - after),
                )
        quantile_drift[target] = float(largest_quantile_drift)
    return mean_drift, quantile_drift


def _reduce_scenario_set(
    scenario_set: ScenarioSet,
    top_k: int,
    *,
    quantiles: tuple[float, ...],
    max_mean_drift: float | None,
    max_quantile_drift: float | None,
    preserve_critical_events: bool,
) -> tuple[ScenarioSet, ReductionDiagnostics]:
    if not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if any(not 0.0 < quantile < 1.0 for quantile in quantiles):
        raise ValueError("diagnostic quantiles must be within (0, 1)")
    if (
        max_mean_drift is not None
        and (
            not np.isfinite(max_mean_drift)
            or max_mean_drift < 0.0
        )
    ):
        raise ValueError("max_mean_drift must be finite and non-negative")
    if (
        max_quantile_drift is not None
        and (
            not np.isfinite(max_quantile_drift)
            or max_quantile_drift < 0.0
        )
    ):
        raise ValueError(
            "max_quantile_drift must be finite and non-negative"
        )

    count = len(scenario_set.scenarios)
    target_count = min(top_k, count)
    vectors = _joint_vectors(scenario_set)
    distances = cdist(vectors, vectors, metric="cityblock")
    original_weights = np.asarray(
        [item.probability for item in scenario_set.scenarios],
        dtype=float,
    )
    peak_index, ramp_index = _critical_event_indices(scenario_set)
    protected = (
        {peak_index, ramp_index}
        if preserve_critical_events
        else set()
    )
    if len(protected) > target_count:
        raise ValueError(
            "top_k is too small to retain distinct critical peak/ramp events"
        )

    active = list(range(count))
    while len(active) > target_count:
        removable = [index for index in active if index not in protected]
        if not removable:
            raise ValueError(
                "top_k is too small to retain critical peak/ramp events"
            )
        best_candidate: int | None = None
        best_cost = np.inf
        for candidate in removable:
            trial = [index for index in active if index != candidate]
            nearest_distances = distances[:, trial].min(axis=1)
            cost = float(original_weights @ nearest_distances)
            if cost < best_cost - 1e-12:
                best_cost = cost
                best_candidate = candidate
        assert best_candidate is not None
        active.remove(best_candidate)

    assignments: dict[int, int] = {}
    for original_index in range(count):
        if original_index in active:
            assignments[original_index] = original_index
        else:
            nearest_position = int(
                np.argmin(distances[original_index, active])
            )
            assignments[original_index] = active[nearest_position]
    retained_weights = {
        retained_index: float(
            sum(
                original_weights[original_index]
                for original_index, assigned_index in assignments.items()
                if assigned_index == retained_index
            )
        )
        for retained_index in active
    }
    total = sum(retained_weights.values())
    retained_weights = {
        index: weight / total
        for index, weight in retained_weights.items()
    }

    probability_transfers = {
        scenario_set.scenarios[original_index].scenario_id:
            scenario_set.scenarios[assigned_index].scenario_id
        for original_index, assigned_index in assignments.items()
        if original_index != assigned_index
    }
    reduced_scenarios = tuple(
        Scenario(
            scenario_id=scenario_set.scenarios[index].scenario_id,
            probability=retained_weights[index],
            issue_time=scenario_set.scenarios[index].issue_time,
            trajectories={
                target: trajectory.copy()
                for target, trajectory
                in scenario_set.scenarios[index].trajectories.items()
            },
            seed=scenario_set.scenarios[index].seed,
            source_versions=dict(
                scenario_set.scenarios[index].source_versions
            ),
        )
        for index in active
    )
    preliminary = ScenarioSet(
        horizon=scenario_set.horizon,
        valid_time_index=scenario_set.valid_time_index,
        units=dict(scenario_set.units),
        scenarios=reduced_scenarios,
        metadata=dict(scenario_set.metadata),
    )
    mean_drift, quantile_drift = _distribution_drift(
        scenario_set,
        preliminary,
        quantiles,
    )
    wasserstein_l1 = float(
        sum(
            original_weights[original_index]
            * distances[original_index, assigned_index]
            for original_index, assigned_index in assignments.items()
        )
    )
    retained_ids = {
        item.scenario_id for item in preliminary.scenarios
    }
    peak_id = scenario_set.scenarios[peak_index].scenario_id
    ramp_id = scenario_set.scenarios[ramp_index].scenario_id
    diagnostics = ReductionDiagnostics(
        original_count=count,
        retained_count=len(preliminary.scenarios),
        wasserstein_l1=wasserstein_l1,
        probability_transfers=probability_transfers,
        mean_drift=mean_drift,
        quantile_drift=quantile_drift,
        critical_peak_scenario_id=peak_id,
        critical_ramp_scenario_id=ramp_id,
        critical_events_retained=(
            peak_id in retained_ids and ramp_id in retained_ids
        ),
    )
    if (
        max_mean_drift is not None
        and any(
            drift > max_mean_drift
            for drift in diagnostics.mean_drift.values()
        )
    ):
        raise ValueError(
            "scenario reduction mean drift exceeds max_mean_drift"
        )
    if (
        max_quantile_drift is not None
        and any(
            drift > max_quantile_drift
            for drift in diagnostics.quantile_drift.values()
        )
    ):
        raise ValueError(
            "scenario reduction quantile drift exceeds "
            "max_quantile_drift"
        )

    metadata = dict(scenario_set.metadata)
    metadata["reduction"] = asdict(diagnostics)
    reduced = ScenarioSet(
        horizon=preliminary.horizon,
        valid_time_index=preliminary.valid_time_index,
        units=dict(preliminary.units),
        scenarios=preliminary.scenarios,
        metadata=metadata,
    )
    return reduced, diagnostics


def reduce_scenarios(
    scenarios: ScenarioSet,
    top_k: int,
    *,
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
    max_mean_drift: float | None = None,
    max_quantile_drift: float | None = None,
    preserve_critical_events: bool = True,
    return_diagnostics: bool = False,
):
    """Reduce joint scenarios by backward Wasserstein L1 selection.

    ``ScenarioSet`` 是唯一 canonical API（v3 D-007）；legacy
    ``PriceScenario`` 兼容路径已随迁移完成删除。
    """
    reduced, diagnostics = _reduce_scenario_set(
        scenarios,
        top_k,
        quantiles=quantiles,
        max_mean_drift=max_mean_drift,
        max_quantile_drift=max_quantile_drift,
        preserve_critical_events=preserve_critical_events,
    )
    if return_diagnostics:
        return reduced, diagnostics
    return reduced
