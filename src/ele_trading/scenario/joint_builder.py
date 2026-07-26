"""Joint scenario construction from aligned forecast contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.stats.qmc import LatinHypercube

from ele_trading.forecasting.contracts import ForecastResult

from .contracts import Scenario, ScenarioSet


_ROLE_EXPECTED_TARGETS = {
    "price": {"price"},
    "load": {"load", "load_power"},
    "wind": {"wind", "wind_power"},
    "pv": {"pv", "pv_power", "solar", "solar_power"},
}
_NON_NEGATIVE_ROLES = {"load", "wind", "pv"}


def _validate_forecasts(
    forecasts_by_role: Mapping[str, ForecastResult],
) -> tuple[tuple[str, ...], int]:
    first: ForecastResult | None = None
    target_names: list[str] = []
    for role, result in forecasts_by_role.items():
        if not isinstance(result, ForecastResult):
            raise ValueError(
                f"{role}_forecast must be a ForecastResult"
            )
        if result.request.target not in _ROLE_EXPECTED_TARGETS[role]:
            raise ValueError(
                f"{role}_forecast has incompatible target "
                f"{result.request.target!r}"
            )
        issue_time = pd.Timestamp(result.request.issue_time)
        if pd.isna(issue_time) or issue_time.tzinfo is None:
            raise ValueError(
                f"{role}_forecast request issue_time must be timezone-aware"
            )
        feature_as_of = pd.Timestamp(result.feature_as_of)
        if pd.isna(feature_as_of) or feature_as_of.tzinfo is None:
            raise ValueError(
                f"{role}_forecast feature_as_of must be timezone-aware"
            )
        if feature_as_of > issue_time:
            raise ValueError(
                f"{role}_forecast feature_as_of must not exceed issue_time"
            )
        if first is None:
            first = result
        else:
            if not result.point.index.equals(first.point.index):
                raise ValueError(
                    "forecast sources must use an aligned valid-time index"
                )
            if result.request.issue_time != first.request.issue_time:
                raise ValueError(
                    "forecast sources must use a consistent issue_time"
                )
        target_names.append(result.request.target)
    assert first is not None
    if len(target_names) != len(set(target_names)):
        raise ValueError("forecast targets must be unique")
    return tuple(target_names), len(first.point)


def _validate_correlation(
    correlation_matrix: np.ndarray | None,
    target_dimension: int,
    full_dimension: int,
) -> tuple[np.ndarray, str]:
    if correlation_matrix is None:
        return np.eye(target_dimension), "target"
    matrix = np.asarray(correlation_matrix, dtype=float)
    if matrix.shape == (target_dimension, target_dimension):
        scope = "target"
    elif matrix.shape == (full_dimension, full_dimension):
        scope = "target_time"
    else:
        raise ValueError(
            "correlation_matrix must have target or target-time shape "
            f"({target_dimension}, {target_dimension}) or "
            f"({full_dimension}, {full_dimension})"
        )
    if not np.isfinite(matrix).all():
        raise ValueError("correlation_matrix must contain finite values")
    if not np.allclose(matrix, matrix.T, atol=1e-10, rtol=0.0):
        raise ValueError("correlation_matrix must be symmetric")
    if not np.allclose(np.diag(matrix), 1.0, atol=1e-10, rtol=0.0):
        raise ValueError("correlation_matrix diagonal must equal 1")
    if (np.abs(matrix) > 1.0 + 1e-10).any():
        raise ValueError(
            "correlation_matrix coefficients must be within [-1, 1]"
        )
    eigenvalues = np.linalg.eigvalsh(matrix)
    if eigenvalues.min() < -1e-9:
        raise ValueError(
            "correlation_matrix must be positive semidefinite"
        )
    return matrix, scope


def _correlation_factor(matrix: np.ndarray) -> np.ndarray:
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    clipped = np.clip(eigenvalues, 0.0, None)
    return eigenvectors @ np.diag(np.sqrt(clipped))


def _requested_levels(
    requested_quantiles: (
        Mapping[str, Sequence[float]]
        | Sequence[float]
        | None
    ),
    role: str,
    target: str,
    result: ForecastResult,
) -> tuple[float, ...]:
    if requested_quantiles is None:
        levels = tuple(result.quantiles)
    elif isinstance(requested_quantiles, Mapping):
        levels = tuple(
            requested_quantiles.get(
                target,
                requested_quantiles.get(role, tuple(result.quantiles)),
            )
        )
    else:
        levels = tuple(requested_quantiles)
    if (
        any(not 0.0 < float(level) < 1.0 for level in levels)
        or tuple(sorted(levels)) != levels
        or len(set(levels)) != len(levels)
    ):
        raise ValueError(
            "requested quantiles must be ordered, unique, and within (0, 1)"
        )
    missing = [level for level in levels if level not in result.quantiles]
    if missing:
        raise ValueError(
            f"forecast target {target!r} does not provide quantiles {missing}"
        )
    return levels


def _scale_for_target(
    residual_scales: Mapping[str, float | Sequence[float]] | None,
    role: str,
    target: str,
    result: ForecastResult,
    levels: tuple[float, ...],
) -> np.ndarray:
    explicit = None
    if residual_scales is not None:
        explicit = residual_scales.get(
            target,
            residual_scales.get(role),
        )
    if explicit is not None:
        scale = np.asarray(explicit, dtype=float)
        if scale.ndim == 0:
            scale = np.full(len(result.point), float(scale))
        if scale.shape != (len(result.point),):
            raise ValueError(
                f"residual scale for {target!r} must be scalar or horizon-sized"
            )
        if not np.isfinite(scale).all() or (scale < 0.0).any():
            raise ValueError(
                f"residual scale for {target!r} must be finite and non-negative"
            )
        return scale

    inferred: list[np.ndarray] = []
    point = result.point.to_numpy(dtype=float)
    for level in levels:
        z_value = float(norm.ppf(level))
        if abs(z_value) < 1e-12:
            continue
        residual = (
            result.quantiles[level].to_numpy(dtype=float) - point
        ) / z_value
        inferred.append(np.abs(residual))
    if not inferred:
        return np.zeros(len(point), dtype=float)
    return np.median(np.vstack(inferred), axis=0)


def _inverse_marginal(
    uniforms: np.ndarray,
    result: ForecastResult,
    levels: tuple[float, ...],
    residual_scale: np.ndarray,
) -> np.ndarray:
    point = result.point.to_numpy(dtype=float)
    output = np.empty_like(uniforms, dtype=float)
    for time_index in range(len(point)):
        anchors = {
            float(level): float(result.quantiles[level].iloc[time_index])
            for level in levels
        }
        if 0.5 in result.quantiles:
            anchors[0.5] = float(
                result.quantiles[0.5].iloc[time_index]
            )
        else:
            anchors[0.5] = float(point[time_index])
        sorted_levels = np.array(sorted(anchors), dtype=float)
        sorted_values = np.array(
            [anchors[level] for level in sorted_levels],
            dtype=float,
        )
        sorted_values = np.maximum.accumulate(sorted_values)
        values = np.interp(
            uniforms[:, time_index],
            sorted_levels,
            sorted_values,
        )
        lower = uniforms[:, time_index] < sorted_levels[0]
        upper = uniforms[:, time_index] > sorted_levels[-1]
        scale = float(residual_scale[time_index])
        if scale > 0.0:
            normal_values = norm.ppf(
                np.clip(uniforms[:, time_index], 1e-10, 1.0 - 1e-10)
            )
            if lower.any():
                values[lower] = (
                    sorted_values[0]
                    + scale
                    * (
                        normal_values[lower]
                        - norm.ppf(sorted_levels[0])
                    )
                )
            if upper.any():
                values[upper] = (
                    sorted_values[-1]
                    + scale
                    * (
                        normal_values[upper]
                        - norm.ppf(sorted_levels[-1])
                    )
                )
        output[:, time_index] = values
    return output


def build_joint_scenarios(
    price_forecast: ForecastResult,
    load_forecast: ForecastResult,
    wind_forecast: ForecastResult,
    pv_forecast: ForecastResult,
    *,
    num_scenarios: int,
    requested_quantiles: (
        Mapping[str, Sequence[float]]
        | Sequence[float]
        | None
    ) = None,
    residual_scales: Mapping[str, float | Sequence[float]] | None = None,
    correlation_matrix: np.ndarray | None = None,
    method: str = "lhs",
    random_seed: int = 7,
) -> ScenarioSet:
    """Build reproducible price/load/wind/PV scenarios with a Gaussian copula."""
    if not isinstance(num_scenarios, int) or num_scenarios <= 0:
        raise ValueError("num_scenarios must be a positive integer")
    if method not in {"lhs", "mc"}:
        raise ValueError("method must be 'lhs' or 'mc'")
    if not isinstance(random_seed, (int, np.integer)):
        raise ValueError("random_seed must be an integer")

    forecasts_by_role = {
        "price": price_forecast,
        "load": load_forecast,
        "wind": wind_forecast,
        "pv": pv_forecast,
    }
    target_names, horizon = _validate_forecasts(forecasts_by_role)
    dimension = horizon * len(forecasts_by_role)
    correlation, correlation_scope = _validate_correlation(
        correlation_matrix,
        len(forecasts_by_role),
        dimension,
    )
    if method == "lhs":
        uniforms = LatinHypercube(
            d=dimension,
            seed=int(random_seed),
        ).random(n=num_scenarios)
        independent_normals = norm.ppf(
            np.clip(uniforms, 1e-10, 1.0 - 1e-10)
        )
    else:
        independent_normals = np.random.default_rng(
            int(random_seed)
        ).standard_normal((num_scenarios, dimension))
    factor = _correlation_factor(correlation)
    if correlation_scope == "target_time":
        correlated_normals = (
            independent_normals @ factor.T
        ).reshape(
            num_scenarios,
            horizon,
            len(forecasts_by_role),
        )
    else:
        independent_normals = independent_normals.reshape(
            num_scenarios,
            horizon,
            len(forecasts_by_role),
        )
        correlated_normals = independent_normals @ factor.T
    correlated_uniforms = norm.cdf(correlated_normals)

    matrices: dict[str, np.ndarray] = {}
    for target_index, (role, result) in enumerate(
        forecasts_by_role.items()
    ):
        target = result.request.target
        levels = _requested_levels(
            requested_quantiles,
            role,
            target,
            result,
        )
        scale = _scale_for_target(
            residual_scales,
            role,
            target,
            result,
            levels,
        )
        values = _inverse_marginal(
            correlated_uniforms[:, :, target_index],
            result,
            levels,
            scale,
        )
        if role in _NON_NEGATIVE_ROLES:
            values = np.maximum(values, 0.0)
        matrices[target] = values

    probability = 1.0 / num_scenarios
    source_versions = {
        result.request.target: result.model_version
        for result in forecasts_by_role.values()
    }
    scenarios = tuple(
        Scenario(
            scenario_id=f"scenario_{index:04d}",
            probability=probability,
            issue_time=price_forecast.request.issue_time,
            trajectories={
                target: price_forecast.point.__class__(
                    matrix[index],
                    index=price_forecast.point.index,
                    dtype=float,
                )
                for target, matrix in matrices.items()
            },
            seed=int(random_seed),
            source_versions=source_versions,
        )
        for index in range(num_scenarios)
    )
    return ScenarioSet(
        horizon=horizon,
        valid_time_index=price_forecast.point.index,
        units={
            result.request.target: result.unit
            for result in forecasts_by_role.values()
        },
        scenarios=scenarios,
        metadata={
            "sampling_method": method,
            "random_seed": int(random_seed),
            "target_order": target_names,
            "correlation_matrix": correlation.tolist(),
            "correlation_scope": correlation_scope,
            "source_feature_as_of": {
                result.request.target: result.feature_as_of.isoformat()
                for result in forecasts_by_role.values()
            },
        },
    )
