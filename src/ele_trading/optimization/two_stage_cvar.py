"""Runnable PuLP two-stage BESS optimizer with weighted CVaR risk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from pulp import (
    LpAffineExpression,
    LpMinimize,
    LpProblem,
    LpVariable,
    lpSum,
    value,
)

from ele_trading.scenario.contracts import Scenario, ScenarioSet

from .bess_model import BESSConfig, BESSVariables, add_bess_constraints
from .risk import (
    CVaRAuxiliaries,
    add_cvar_auxiliaries,
    risk_adjusted_objective,
    weighted_var_cvar,
)
from .solver import SolveStatus, SolverResult, solve_pulp_model


_TARGET_ALIASES = {
    "price": ("price",),
    "load": ("load", "load_power"),
    "wind": ("wind", "wind_power"),
    "pv": ("pv", "pv_power", "solar", "solar_power"),
}


@dataclass(frozen=True, slots=True)
class ScenarioRecourse:
    """Solved second-stage battery and deviation decisions."""

    scenario_id: str
    probability: float
    p_charge: list[float]
    p_discharge: list[float]
    soc: list[float]
    net_export: list[float]
    deviation_positive: list[float]
    deviation_negative: list[float]
    cost: float


@dataclass(frozen=True, slots=True)
class TwoStageCVaRResult:
    """Typed solve result; failed solves contain no fabricated schedules."""

    solve_status: SolveStatus
    solver_result: SolverResult
    first_stage_bid: list[float] | None
    scenario_recourse: dict[str, ScenarioRecourse]
    expected_cost: float | None
    var: float | None
    cvar: float | None
    objective_value: float | None
    trace_metadata: dict[str, object]

    @property
    def first_stage_schedule(self) -> list[float] | None:
        return self.first_stage_bid


@dataclass(slots=True)
class _ProblemContext:
    model: LpProblem
    first_stage_bid: dict[int, LpVariable]
    bess: dict[str, BESSVariables]
    net_export: dict[str, dict[int, object]]
    deviation_positive: dict[str, dict[int, LpVariable]]
    deviation_negative: dict[str, dict[int, LpVariable]]
    losses: dict[str, LpAffineExpression]
    expected_cost: LpAffineExpression
    cvar: CVaRAuxiliaries
    day_ahead_prices: tuple[float, ...]


def _revalidate_scenario_set(scenario_set: ScenarioSet) -> ScenarioSet:
    if not isinstance(scenario_set, ScenarioSet):
        raise ValueError("scenario_set must be a ScenarioSet")
    scenarios = tuple(
        Scenario(
            scenario_id=item.scenario_id,
            probability=item.probability,
            issue_time=item.issue_time,
            trajectories={
                target: trajectory.copy()
                for target, trajectory in item.trajectories.items()
            },
            seed=item.seed,
            source_versions=dict(item.source_versions),
        )
        for item in scenario_set.scenarios
    )
    return ScenarioSet(
        horizon=scenario_set.horizon,
        valid_time_index=scenario_set.valid_time_index,
        units=dict(scenario_set.units),
        scenarios=scenarios,
        metadata=dict(scenario_set.metadata),
    )


def _target_name(scenario_set: ScenarioSet, role: str) -> str:
    for target in _TARGET_ALIASES[role]:
        if target in scenario_set.units:
            return target
    raise ValueError(
        f"ScenarioSet must contain a {role} trajectory"
    )


def _day_ahead_prices(
    scenario_set: ScenarioSet,
    price_target: str,
    supplied: Sequence[float] | pd.Series | None,
) -> tuple[float, ...]:
    if supplied is None:
        values = sum(
            (
                item.probability
                * item.trajectories[price_target].to_numpy(dtype=float)
                for item in scenario_set.scenarios
            ),
            np.zeros(scenario_set.horizon, dtype=float),
        )
    elif isinstance(supplied, pd.Series):
        if not supplied.index.equals(scenario_set.valid_time_index):
            raise ValueError(
                "day_ahead_prices index must match ScenarioSet valid times"
            )
        values = supplied.to_numpy(dtype=float)
    else:
        values = np.asarray(supplied, dtype=float)
    if values.shape != (scenario_set.horizon,):
        raise ValueError(
            "day_ahead_prices length must match ScenarioSet horizon"
        )
    if not np.isfinite(values).all():
        raise ValueError("day_ahead_prices must be finite")
    return tuple(float(item) for item in values)


def _build_problem(
    scenario_set: ScenarioSet,
    *,
    bess_config: BESSConfig,
    day_ahead_prices: Sequence[float] | pd.Series | None,
    alpha: float,
    risk_weight: float,
    degradation_cost: float,
    deviation_penalty_positive: float,
    deviation_penalty_negative: float,
) -> _ProblemContext:
    if not isinstance(scenario_set, ScenarioSet):
        raise ValueError("scenario_set must be a ScenarioSet")
    for name, amount in {
        "degradation_cost": degradation_cost,
        "deviation_penalty_positive": deviation_penalty_positive,
        "deviation_penalty_negative": deviation_penalty_negative,
    }.items():
        if not np.isfinite(amount) or amount < 0.0:
            raise ValueError(f"{name} must be finite and non-negative")

    price_target = _target_name(scenario_set, "price")
    load_target = _target_name(scenario_set, "load")
    wind_target = _target_name(scenario_set, "wind")
    pv_target = _target_name(scenario_set, "pv")
    day_ahead = _day_ahead_prices(
        scenario_set,
        price_target,
        day_ahead_prices,
    )
    steps = tuple(range(scenario_set.horizon))
    model = LpProblem("two_stage_bess_cvar", LpMinimize)

    max_export_by_step = {
        step: max(
            0.0,
            max(
                float(
                    item.trajectories[wind_target].iloc[step]
                    + item.trajectories[pv_target].iloc[step]
                    - item.trajectories[load_target].iloc[step]
                    + bess_config.p_dis_max
                )
                for item in scenario_set.scenarios
            ),
        )
        for step in steps
    }
    bid = {
        step: LpVariable(
            f"first_stage_bid_{step}",
            lowBound=0.0,
            upBound=max_export_by_step[step],
        )
        for step in steps
    }
    bess_by_scenario: dict[str, BESSVariables] = {}
    net_export_by_scenario: dict[str, dict[int, object]] = {}
    positive_by_scenario: dict[str, dict[int, LpVariable]] = {}
    negative_by_scenario: dict[str, dict[int, LpVariable]] = {}
    loss_by_scenario: dict[str, LpAffineExpression] = {}

    for scenario_index, scenario in enumerate(scenario_set.scenarios):
        net_load = {
            step: float(
                scenario.trajectories[load_target].iloc[step]
                - scenario.trajectories[wind_target].iloc[step]
                - scenario.trajectories[pv_target].iloc[step]
            )
            for step in steps
        }
        bess = add_bess_constraints(
            model,
            steps,
            bess_config,
            net_load=net_load if bess_config.no_export else None,
            prefix=f"recourse_{scenario_index}",
        )
        net_export = {
            step: (
                -net_load[step]
                + bess.p_discharge[step]
                - bess.p_charge[step]
            )
            for step in steps
        }
        positive = {
            step: LpVariable(
                f"deviation_positive_{scenario_index}_{step}",
                lowBound=0.0,
            )
            for step in steps
        }
        negative = {
            step: LpVariable(
                f"deviation_negative_{scenario_index}_{step}",
                lowBound=0.0,
            )
            for step in steps
        }
        for step in steps:
            model += (
                net_export[step] - bid[step]
                == positive[step] - negative[step],
                f"deviation_balance_{scenario_index}_{step}",
            )
        revenue = lpSum(
            (
                day_ahead[step] * bid[step]
                + float(
                    scenario.trajectories[price_target].iloc[step]
                )
                * (net_export[step] - bid[step])
                - deviation_penalty_positive * positive[step]
                - deviation_penalty_negative * negative[step]
                - degradation_cost
                * (
                    bess.p_charge[step]
                    + bess.p_discharge[step]
                )
            )
            * bess_config.dt
            for step in steps
        )
        bess_by_scenario[scenario.scenario_id] = bess
        net_export_by_scenario[scenario.scenario_id] = net_export
        positive_by_scenario[scenario.scenario_id] = positive
        negative_by_scenario[scenario.scenario_id] = negative
        loss_by_scenario[scenario.scenario_id] = -revenue

    probabilities = {
        item.scenario_id: item.probability
        for item in scenario_set.scenarios
    }
    expected_cost = lpSum(
        probabilities[scenario_id] * loss
        for scenario_id, loss in loss_by_scenario.items()
    )
    cvar = add_cvar_auxiliaries(
        model,
        loss_by_scenario,
        probabilities,
        alpha=alpha,
    )
    model += risk_adjusted_objective(
        expected_cost,
        cvar.expression,
        risk_weight=risk_weight,
    )
    return _ProblemContext(
        model=model,
        first_stage_bid=bid,
        bess=bess_by_scenario,
        net_export=net_export_by_scenario,
        deviation_positive=positive_by_scenario,
        deviation_negative=negative_by_scenario,
        losses=loss_by_scenario,
        expected_cost=expected_cost,
        cvar=cvar,
        day_ahead_prices=day_ahead,
    )


def solve_two_stage_cvar(
    scenario_set: ScenarioSet,
    *,
    bess_config: BESSConfig,
    deviation_penalty_positive: float,
    deviation_penalty_negative: float,
    day_ahead_prices: Sequence[float] | pd.Series | None = None,
    alpha: float = 0.95,
    risk_weight: float = 1.0,
    degradation_cost: float = 0.01,
    solver=None,
) -> TwoStageCVaRResult:
    """Build and solve the two-stage problem through PuLP/CBC."""
    try:
        scenario_set = _revalidate_scenario_set(scenario_set)
    except (TypeError, ValueError) as exc:
        solver_result = SolverResult(
            status=SolveStatus.ERROR,
            objective_value=None,
            raw_status=None,
            solver_name=(
                solver.__class__.__name__
                if solver is not None
                else "PULP_CBC_CMD"
            ),
            message=str(exc),
        )
        return TwoStageCVaRResult(
            solve_status=SolveStatus.ERROR,
            solver_result=solver_result,
            first_stage_bid=None,
            scenario_recourse={},
            expected_cost=None,
            var=None,
            cvar=None,
            objective_value=None,
            trace_metadata={"validation_error": str(exc)},
        )
    context = _build_problem(
        scenario_set,
        bess_config=bess_config,
        day_ahead_prices=day_ahead_prices,
        alpha=alpha,
        risk_weight=risk_weight,
        degradation_cost=degradation_cost,
        deviation_penalty_positive=deviation_penalty_positive,
        deviation_penalty_negative=deviation_penalty_negative,
    )
    solver_result = solve_pulp_model(
        context.model,
        solver=solver,
    )
    trace_metadata = {
        "scenario_issue_time": scenario_set.issue_time.isoformat(),
        "scenario_ids": [
            item.scenario_id for item in scenario_set.scenarios
        ],
        "scenario_source_versions": scenario_set.source_versions,
        "scenario_metadata": dict(scenario_set.metadata),
        "day_ahead_prices": list(context.day_ahead_prices),
        "alpha": float(alpha),
        "risk_weight": float(risk_weight),
        "dt": bess_config.dt,
        "solver_name": solver_result.solver_name,
    }
    if solver_result.status is not SolveStatus.OPTIMAL:
        return TwoStageCVaRResult(
            solve_status=solver_result.status,
            solver_result=solver_result,
            first_stage_bid=None,
            scenario_recourse={},
            expected_cost=None,
            var=None,
            cvar=None,
            objective_value=None,
            trace_metadata=trace_metadata,
        )

    steps = tuple(range(scenario_set.horizon))
    scenario_lookup = {
        item.scenario_id: item
        for item in scenario_set.scenarios
    }
    recourse: dict[str, ScenarioRecourse] = {}
    for scenario_id, bess in context.bess.items():
        scenario = scenario_lookup[scenario_id]
        recourse[scenario_id] = ScenarioRecourse(
            scenario_id=scenario_id,
            probability=scenario.probability,
            p_charge=[
                float(value(bess.p_charge[step])) for step in steps
            ],
            p_discharge=[
                float(value(bess.p_discharge[step])) for step in steps
            ],
            soc=[
                float(value(bess.soc[step])) for step in steps
            ],
            net_export=[
                float(value(context.net_export[scenario_id][step]))
                for step in steps
            ],
            deviation_positive=[
                float(
                    value(
                        context.deviation_positive[scenario_id][step]
                    )
                )
                for step in steps
            ],
            deviation_negative=[
                float(
                    value(
                        context.deviation_negative[scenario_id][step]
                    )
                )
                for step in steps
            ],
            cost=float(value(context.losses[scenario_id])),
        )
    reported_var, reported_cvar = weighted_var_cvar(
        {
            scenario_id: item.cost
            for scenario_id, item in recourse.items()
        },
        {
            scenario_id: item.probability
            for scenario_id, item in recourse.items()
        },
        alpha=alpha,
    )
    return TwoStageCVaRResult(
        solve_status=solver_result.status,
        solver_result=solver_result,
        first_stage_bid=[
            float(value(context.first_stage_bid[step]))
            for step in steps
        ],
        scenario_recourse=recourse,
        expected_cost=float(value(context.expected_cost)),
        var=reported_var,
        cvar=reported_cvar,
        objective_value=solver_result.objective_value,
        trace_metadata=trace_metadata,
    )


def build_two_stage_cvar_model(
    T,
    OMEGA,
    p_omega: Mapping,
    pi_da: Mapping,
    pi_rt: Mapping,
    soc0: float,
    soc_min: float,
    soc_max: float,
    p_ch_max: float,
    p_dis_max: float,
    eta_ch: float,
    eta_dis: float,
    deg_cost: float,
    *,
    kappa_pos: float,
    kappa_neg: float,
    dt: float = 1.0,
    alpha: float = 0.95,
    lam: float = 1.0,
) -> LpProblem:
    """Narrow unsolved adapter for the active v1 example builder."""
    time_steps = tuple(T)
    scenario_ids = tuple(OMEGA)
    if not time_steps or not scenario_ids:
        raise ValueError("T and OMEGA must not be empty")
    issue_time = pd.Timestamp("2000-01-01", tz="UTC")
    index = pd.date_range(
        issue_time + pd.Timedelta(hours=dt),
        periods=len(time_steps),
        freq=pd.Timedelta(hours=dt),
    )
    scenario_set = ScenarioSet(
        horizon=len(time_steps),
        valid_time_index=index,
        units={
            "price": "unknown",
            "load": "MW",
            "wind_power": "MW",
            "pv_power": "MW",
        },
        scenarios=tuple(
            Scenario(
                scenario_id=str(scenario_id),
                probability=float(p_omega[scenario_id]),
                issue_time=issue_time,
                trajectories={
                    "price": pd.Series(
                        [
                            pi_rt[(step, scenario_id)]
                            for step in time_steps
                        ],
                        index=index,
                        dtype=float,
                    ),
                    "load": pd.Series(0.0, index=index),
                    "wind_power": pd.Series(0.0, index=index),
                    "pv_power": pd.Series(0.0, index=index),
                },
                seed=0,
                source_versions={
                    "price": "legacy-pi-rt",
                    "load": "legacy-zero",
                    "wind_power": "legacy-zero",
                    "pv_power": "legacy-zero",
                },
            )
            for scenario_id in scenario_ids
        ),
        metadata={"compatibility": "build_two_stage_cvar_model"},
    )
    return _build_problem(
        scenario_set,
        bess_config=BESSConfig(
            soc0=soc0,
            soc_min=soc_min,
            soc_max=soc_max,
            p_ch_max=p_ch_max,
            p_dis_max=p_dis_max,
            eta_ch=eta_ch,
            eta_dis=eta_dis,
            dt=dt,
        ),
        day_ahead_prices=[pi_da[step] for step in time_steps],
        alpha=alpha,
        risk_weight=lam,
        degradation_cost=deg_cost,
        deviation_penalty_positive=kappa_pos,
        deviation_penalty_negative=kappa_neg,
    ).model
