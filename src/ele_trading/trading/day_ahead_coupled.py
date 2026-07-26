"""Next-day physical resource planning for Mengxi single settlement."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Mapping

import numpy as np
import pandas as pd
from pulp import LpMinimize, LpProblem, lpSum, value

from ele_trading.optimization.bess_model import (
    BESSConfig,
    add_bess_constraints,
)
from ele_trading.optimization.risk import add_cvar_auxiliaries
from ele_trading.optimization.solver import (
    SolveStatus,
    solve_pulp_model,
)
from ele_trading.scenario.contracts import ScenarioSet
from ele_trading.trading.contracts import (
    DecisionTrace,
    MarketConfig,
    OperationalPlan,
)
from ele_trading.trading.settlement_mengxi import (
    compute_contract_difference,
)


def _finite_vector(value: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if result.ndim != 1 or not len(result):
        raise ValueError(f"{name} must be a non-empty one-dimensional array")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} must contain finite values")
    return result


def _solver_version() -> str:
    try:
        return version("pulp")
    except PackageNotFoundError:
        return "unknown"


def _scenario_period_energy(
    scenario_set: ScenarioSet,
    scenario,
    target: str,
    *,
    dt: float,
) -> np.ndarray:
    values = scenario.trajectories[target].to_numpy(dtype=float)
    if scenario_set.units[target] == "MW":
        return values * dt
    return values


def _constraint_trace(
    schedule: pd.DataFrame,
    soc: pd.Series,
    *,
    bess: Mapping[str, float],
    tolerance: float = 1e-6,
) -> dict[str, tuple[int, ...]]:
    trace = {
        "soc_min": tuple(
            int(i)
            for i, value_ in enumerate(soc.iloc[1:])
            if value_ <= float(bess["socmin"]) + tolerance
        ),
        "soc_max": tuple(
            int(i)
            for i, value_ in enumerate(soc.iloc[1:])
            if value_ >= float(bess["socmax"]) - tolerance
        ),
        "charge_limit": tuple(
            int(i)
            for i, value_ in enumerate(schedule["p_charge"])
            if value_
            >= float(bess["p_bcmax"]) - tolerance
        ),
        "discharge_limit": tuple(
            int(i)
            for i, value_ in enumerate(schedule["p_discharge"])
            if value_
            >= float(bess["p_bdmax"]) - tolerance
        ),
    }
    return {name: periods for name, periods in trace.items() if periods}


def solve_day_ahead_operational(
    load_forecast: np.ndarray,
    realtime_price_forecast: np.ndarray,
    bess: Mapping[str, float],
    config: MarketConfig,
    *,
    explanatory_price_signal: np.ndarray | None = None,
    q_long: np.ndarray | None = None,
    p_long: np.ndarray | None = None,
    p_ref: np.ndarray | None = None,
    scenario_set: ScenarioSet | None = None,
    dr_adjustment: float = 0.0,
    decision_time: pd.Timestamp | None = None,
    input_versions: Mapping[str, str] | None = None,
    config_version: str = "runtime-config",
    solver=None,
) -> OperationalPlan:
    """Minimize next-day real-time energy and degradation costs."""
    load = _finite_vector(load_forecast, "load_forecast")
    price = _finite_vector(
        realtime_price_forecast,
        "realtime_price_forecast",
    )
    if load.shape != price.shape:
        raise ValueError("load and price forecasts must use the same horizon")
    if explanatory_price_signal is not None:
        explanatory = _finite_vector(
            explanatory_price_signal,
            "explanatory_price_signal",
        )
        if explanatory.shape != load.shape:
            raise ValueError(
                "explanatory_price_signal must use the planning horizon"
            )
    contract_inputs = (q_long, p_long, p_ref)
    if any(item is not None for item in contract_inputs):
        if not all(item is not None for item in contract_inputs):
            raise ValueError(
                "q_long, p_long and p_ref must be provided together"
            )
        assert q_long is not None
        assert p_long is not None
        assert p_ref is not None
        contract_value = float(
            np.sum(
                compute_contract_difference(
                    _finite_vector(q_long, "q_long"),
                    _finite_vector(p_long, "p_long"),
                    p_ref=_finite_vector(p_ref, "p_ref"),
                )
            )
        )
        if any(
            np.asarray(item).shape != load.shape
            for item in contract_inputs
        ):
            raise ValueError(
                "contract arrays must use the planning horizon"
            )
    else:
        contract_value = 0.0
    if not np.isfinite(dr_adjustment):
        raise ValueError("dr_adjustment must be finite")
    if scenario_set is not None and scenario_set.horizon != len(load):
        raise ValueError("scenario_set horizon must match the plan horizon")

    horizon = len(load)
    steps = tuple(range(horizon))
    margin = config.operational_power_margin
    terminal_soc = (
        float(config.soc_terminal_min)
        if config.soc_terminal_min is not None
        else float(bess["socini"])
    )
    physical = BESSConfig(
        soc0=float(bess["socini"]),
        soc_min=float(bess["socmin"]),
        soc_max=float(bess["socmax"]),
        p_ch_max=margin * float(bess["p_bcmax"]),
        p_dis_max=margin * float(bess["p_bdmax"]),
        eta_ch=float(bess["p_bceff"]),
        eta_dis=float(bess["p_bdeff"]),
        dt=config.dt,
        terminal_soc=terminal_soc,
        max_throughput=(
            config.throughput_max_ratio * 2.0 * float(bess["cap"])
            if config.throughput_max_ratio > 0.0
            else None
        ),
        no_export=True,
    )
    model = LpProblem("day_ahead_operational", LpMinimize)
    variables = add_bess_constraints(
        model,
        steps,
        physical,
        net_load={
            step: float(load[step]) / config.dt
            for step in steps
        },
        prefix="operational",
    )
    energy_cost = lpSum(
        (
            load[step]
            + (
                variables.p_charge[step]
                - variables.p_discharge[step]
            )
            * config.dt
        )
        * price[step]
        for step in steps
    )
    degradation_cost = lpSum(
        config.deg_cost_per_mwh
        * (
            variables.p_charge[step]
            + variables.p_discharge[step]
        )
        * config.dt
        for step in steps
    )
    cvar_expression = None
    if scenario_set is None:
        expected_energy_cost = energy_cost
        expected_cost = (
            energy_cost
            + degradation_cost
            + contract_value
            + dr_adjustment
        )
        model += expected_cost
    else:
        scenario_costs = {}
        scenario_energy_costs = {}
        probabilities = {}
        for scenario in scenario_set.scenarios:
            if "price" not in scenario.trajectories:
                raise ValueError(
                    "scenario trajectories must contain price"
                )
            scenario_price = scenario.trajectories["price"].to_numpy(
                dtype=float
            )
            scenario_load = (
                _scenario_period_energy(
                    scenario_set,
                    scenario,
                    "load",
                    dt=config.dt,
                )
                if "load" in scenario.trajectories
                else load
            )
            for renewable_target in ("wind_power", "pv_power"):
                if renewable_target in scenario.trajectories:
                    scenario_load = scenario_load - _scenario_period_energy(
                        scenario_set,
                        scenario,
                        renewable_target,
                        dt=config.dt,
                    )
            scenario_load = np.maximum(scenario_load, 0.0)
            scenario_energy = lpSum(
                (
                    scenario_load[step]
                    + (
                        variables.p_charge[step]
                        - variables.p_discharge[step]
                    )
                    * config.dt
                )
                * scenario_price[step]
                for step in steps
            )
            scenario_energy_costs[scenario.scenario_id] = scenario_energy
            scenario_costs[scenario.scenario_id] = (
                scenario_energy
                + degradation_cost
                + contract_value
                + dr_adjustment
            )
            probabilities[scenario.scenario_id] = scenario.probability
        expected_energy_cost = lpSum(
            probabilities[scenario_id] * scenario_energy_costs[scenario_id]
            for scenario_id in scenario_energy_costs
        )
        expected_cost = lpSum(
            probabilities[scenario_id] * scenario_costs[scenario_id]
            for scenario_id in scenario_costs
        )
        cvar = add_cvar_auxiliaries(
            model,
            scenario_costs,
            probabilities,
            alpha=config.scenario_cvar_alpha,
            prefix="operational_cvar",
        )
        cvar_expression = cvar.expression
        model += (
            expected_cost
            + config.scenario_cvar_weight * cvar_expression
        )

    solve_result = solve_pulp_model(model, solver=solver)
    if solve_result.status not in {
        SolveStatus.OPTIMAL,
        SolveStatus.FEASIBLE,
    }:
        raise RuntimeError(
            f"day-ahead operational solve failed: {solve_result.status.value}"
        )

    p_charge = np.array(
        [value(variables.p_charge[step]) for step in steps],
        dtype=float,
    )
    p_discharge = np.array(
        [value(variables.p_discharge[step]) for step in steps],
        dtype=float,
    )
    schedule = pd.DataFrame(
        {
            "p_charge": p_charge,
            "p_discharge": p_discharge,
            "p_net": p_discharge - p_charge,
        }
    )
    soc = pd.Series(
        [
            float(bess["socini"]),
            *[
                float(value(variables.soc[step]))
                for step in steps
            ],
        ],
        name="soc",
    )
    active_constraints = _constraint_trace(
        schedule,
        soc,
        bess={
            **bess,
            "p_bcmax": physical.p_ch_max,
            "p_bdmax": physical.p_dis_max,
        },
    )
    energy_value = float(value(expected_energy_cost))
    degradation_value = float(value(degradation_cost))
    expected_cost_value = float(value(expected_cost))
    expected_risk = (
        float(value(cvar_expression))
        if cvar_expression is not None
        else 0.0
    )
    trace = DecisionTrace(
        decision_time=decision_time or pd.Timestamp.now(tz="UTC"),
        input_versions=dict(input_versions or {}),
        model_versions={
            "dispatch": "single-settlement-operational-v1",
        },
        config_version=config_version,
        solver_name=solve_result.solver_name,
        solver_version=_solver_version(),
        solver_status=solve_result.status.value,
        objective_components={
            "energy_cost": energy_value,
            "degradation_cost": degradation_value,
            "contract_difference": contract_value,
            "dr_adjustment": float(dr_adjustment),
            "cvar": expected_risk,
        },
        active_constraints=active_constraints,
    )
    return OperationalPlan(
        resource_schedule=schedule,
        soc=soc,
        expected_cost=expected_cost_value,
        expected_risk=expected_risk,
        constraint_trace=active_constraints,
        decision_trace=trace,
    )
