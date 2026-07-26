"""CVaR auxiliary variables and risk-adjusted objective helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from pulp import LpAffineExpression, LpProblem, LpVariable, lpSum


@dataclass(frozen=True, slots=True)
class CVaRAuxiliaries:
    """Rockafellar-Uryasev VaR threshold, excesses and CVaR expression."""

    var: LpVariable
    excess: dict[str, LpVariable]
    expression: LpAffineExpression


def _validate_probabilities(
    losses: Mapping[str, object],
    probabilities: Mapping[str, float],
) -> dict[str, float]:
    if not losses:
        raise ValueError("losses must not be empty")
    if set(losses) != set(probabilities):
        raise ValueError("probabilities must match loss scenario IDs")
    normalized = {
        scenario_id: float(probability)
        for scenario_id, probability in probabilities.items()
    }
    if any(
        not np.isfinite(probability) or probability <= 0.0
        for probability in normalized.values()
    ):
        raise ValueError(
            "scenario probabilities must be finite and positive"
        )
    if not np.isclose(
        sum(normalized.values()),
        1.0,
        rtol=0.0,
        atol=1e-9,
    ):
        raise ValueError("scenario probabilities must sum to 1")
    return normalized


def add_cvar_auxiliaries(
    model: LpProblem,
    losses: Mapping[str, object],
    probabilities: Mapping[str, float],
    *,
    alpha: float,
    prefix: str = "cvar",
) -> CVaRAuxiliaries:
    """Add weighted upper-tail CVaR auxiliaries for minimization models."""
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    if not np.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be within (0, 1)")
    probabilities = _validate_probabilities(losses, probabilities)
    var = LpVariable(f"{prefix}_var", lowBound=None, upBound=None)
    excess = {
        scenario_id: LpVariable(
            f"{prefix}_excess_{position}",
            lowBound=0.0,
        )
        for position, scenario_id in enumerate(losses)
    }
    for position, (scenario_id, loss) in enumerate(losses.items()):
        model += (
            excess[scenario_id] >= loss - var,
            f"{prefix}_tail_{position}",
        )
    expression = var + (
        1.0 / (1.0 - alpha)
    ) * lpSum(
        probabilities[scenario_id] * excess[scenario_id]
        for scenario_id in losses
    )
    return CVaRAuxiliaries(
        var=var,
        excess=excess,
        expression=expression,
    )


def risk_adjusted_objective(
    expected_cost,
    cvar,
    *,
    risk_weight: float,
):
    """Combine expected cost and CVaR without changing their units."""
    if not np.isfinite(risk_weight) or risk_weight < 0.0:
        raise ValueError("risk_weight must be finite and non-negative")
    return expected_cost + float(risk_weight) * cvar


def weighted_var_cvar(
    losses: Mapping[str, float],
    probabilities: Mapping[str, float],
    *,
    alpha: float,
) -> tuple[float, float]:
    """Evaluate discrete weighted VaR/CVaR independently of LP auxiliaries."""
    if not np.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be within (0, 1)")
    probabilities = _validate_probabilities(losses, probabilities)
    numeric_losses = {
        scenario_id: float(loss)
        for scenario_id, loss in losses.items()
    }
    if any(
        not np.isfinite(loss)
        for loss in numeric_losses.values()
    ):
        raise ValueError("losses must be finite")
    ordered = sorted(
        numeric_losses,
        key=lambda scenario_id: numeric_losses[scenario_id],
    )
    cumulative = 0.0
    var = numeric_losses[ordered[-1]]
    for scenario_id in ordered:
        cumulative += probabilities[scenario_id]
        if cumulative >= alpha - 1e-12:
            var = numeric_losses[scenario_id]
            break
    cvar = var + sum(
        probabilities[scenario_id]
        * max(numeric_losses[scenario_id] - var, 0.0)
        for scenario_id in numeric_losses
    ) / (1.0 - alpha)
    return float(var), float(cvar)
