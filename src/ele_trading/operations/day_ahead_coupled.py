"""Next-day physical resource planning for single-settlement markets."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Mapping

import numpy as np
import pandas as pd
from pulp import LpBinary, LpMinimize, LpProblem, LpVariable, lpSum, value

from ele_trading.optimization.bess_model import (
    BESSConfig,
    add_bess_constraints,
)
from ele_trading.optimization.extraction import extract_bess_values
from ele_trading.optimization.objectives import (
    net_load_energy_cost,
    throughput_degradation_cost,
)
from ele_trading.optimization.risk import add_cvar_auxiliaries
from ele_trading.optimization.solver import (
    SolveStatus,
    solve_pulp_model,
)
from ele_trading.scenario.contracts import ScenarioSet
from ele_trading.domain.contracts import (
    DecisionTrace,
    DRCommitment,
    OperationalPlan,
)
from ele_trading.markets.protocol import SettlementEngine
from ele_trading.markets.sections import MarketConfig


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


# ------------------------------------------------------------------ #
#  模型构建（共享：Pass A / Pass B 用同一组约束和基础目标函数）
# ------------------------------------------------------------------ #

class _ModelBundle:
    """一次模型构建产出的可变对象集合，供 Pass A/B 复用。"""

    __slots__ = (
        "model", "variables", "energy_cost", "degradation_cost",
        "expected_energy_cost", "expected_cost", "cvar_expression",
    )

    def __init__(self) -> None:
        self.model: Any = None
        self.variables: Any = None
        self.energy_cost: Any = None
        self.degradation_cost: Any = None
        self.expected_energy_cost: Any = None
        self.expected_cost: Any = None
        self.cvar_expression: Any = None


def _build_model(
    load: np.ndarray,
    price: np.ndarray,
    bess: Mapping[str, float],
    config: MarketConfig,
    *,
    contract_value: float,
    scenario_set: ScenarioSet | None,
) -> _ModelBundle:
    """构建 BESS 物理约束 + 能量/退化/差价成本目标函数（不含 DR 项）。"""
    horizon = len(load)
    steps = tuple(range(horizon))
    margin = config.bess.operational_power_margin
    terminal_soc = (
        float(config.bess.soc_terminal_min)
        if config.bess.soc_terminal_min is not None
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
        dt=config.market.dt,
        terminal_soc=terminal_soc,
        max_throughput=(
            config.bess.throughput_max_ratio * 2.0 * float(bess["cap"])
            if config.bess.throughput_max_ratio > 0.0
            else None
        ),
        no_export=True,
    )
    bundle = _ModelBundle()
    bundle.model = LpProblem("day_ahead_operational", LpMinimize)
    bundle.variables = add_bess_constraints(
        bundle.model,
        steps,
        physical,
        net_load={
            step: float(load[step]) / config.market.dt
            for step in steps
        },
        prefix="operational",
    )
    bundle.energy_cost = net_load_energy_cost(
        bundle.variables,
        steps,
        load,
        price,
        dt=config.market.dt,
    )
    bundle.degradation_cost = throughput_degradation_cost(
        bundle.variables,
        steps,
        deg_cost_per_mwh=config.bess.deg_cost_per_mwh,
        dt=config.market.dt,
    )
    if scenario_set is None:
        bundle.expected_energy_cost = bundle.energy_cost
        bundle.expected_cost = (
            bundle.energy_cost
            + bundle.degradation_cost
            + contract_value
        )
        bundle.model += bundle.expected_cost
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
                    dt=config.market.dt,
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
                        dt=config.market.dt,
                    )
            scenario_load = np.maximum(scenario_load, 0.0)
            scenario_energy = net_load_energy_cost(
                bundle.variables,
                steps,
                scenario_load,
                scenario_price,
                dt=config.market.dt,
            )
            scenario_energy_costs[scenario.scenario_id] = scenario_energy
            scenario_costs[scenario.scenario_id] = (
                scenario_energy
                + bundle.degradation_cost
                + contract_value
            )
            probabilities[scenario.scenario_id] = scenario.probability
        bundle.expected_energy_cost = lpSum(
            probabilities[sid] * scenario_energy_costs[sid]
            for sid in scenario_energy_costs
        )
        bundle.expected_cost = lpSum(
            probabilities[sid] * scenario_costs[sid]
            for sid in scenario_costs
        )
        cvar = add_cvar_auxiliaries(
            bundle.model,
            scenario_costs,
            probabilities,
            alpha=config.scenario.scenario_cvar_alpha,
            prefix="operational_cvar",
        )
        bundle.cvar_expression = cvar.expression
        bundle.model += (
            bundle.expected_cost
            + config.scenario.scenario_cvar_weight * bundle.cvar_expression
        )
    return bundle


def _solve_and_extract(
    bundle: _ModelBundle,
    *,
    steps: tuple[int, ...],
    bess: Mapping[str, float],
    config: MarketConfig,
    contract_value: float,
    decision_time: pd.Timestamp | None,
    input_versions: Mapping[str, str] | None,
    config_version: str,
    model_tag: str,
    extra_objective: Mapping[str, float] | None = None,
    solver=None,
) -> tuple[pd.DataFrame, pd.Series, dict, float, float, DecisionTrace]:
    """求解模型并提取调度结果 + trace。

    返回 (schedule, soc, active_constraints, expected_cost_value,
          expected_risk, trace)。
    """
    solve_result = solve_pulp_model(bundle.model, solver=solver)
    if solve_result.status not in {
        SolveStatus.OPTIMAL,
        SolveStatus.FEASIBLE,
    }:
        raise RuntimeError(
            f"day-ahead operational solve failed: {solve_result.status.value}"
        )

    values = extract_bess_values(bundle.variables, steps)
    p_charge = np.array(values["p_charge"], dtype=float)
    p_discharge = np.array(values["p_discharge"], dtype=float)
    schedule = pd.DataFrame(
        {
            "p_charge": p_charge,
            "p_discharge": p_discharge,
            "p_net": p_discharge - p_charge,
        }
    )
    soc = pd.Series(
        [float(bess["socini"]), *values["soc"]],
        name="soc",
    )
    margin = config.bess.operational_power_margin
    active_constraints = _constraint_trace(
        schedule,
        soc,
        bess={
            **bess,
            "p_bcmax": margin * float(bess["p_bcmax"]),
            "p_bdmax": margin * float(bess["p_bdmax"]),
        },
    )
    energy_value = float(value(bundle.expected_energy_cost))
    degradation_value = float(value(bundle.degradation_cost))
    expected_cost_value = float(value(bundle.expected_cost))
    expected_risk = (
        float(value(bundle.cvar_expression))
        if bundle.cvar_expression is not None
        else 0.0
    )
    objective_components: dict[str, float] = {
        "energy_cost": energy_value,
        "degradation_cost": degradation_value,
        "contract_difference": contract_value,
        "cvar": expected_risk,
    }
    if extra_objective:
        objective_components.update(extra_objective)
    trace = DecisionTrace(
        decision_time=decision_time or pd.Timestamp.now(tz="UTC"),
        input_versions=dict(input_versions or {}),
        model_versions={"dispatch": model_tag},
        config_version=config_version,
        solver_name=solve_result.solver_name,
        solver_version=_solver_version(),
        solver_status=solve_result.status.value,
        objective_components=objective_components,
        active_constraints=active_constraints,
    )
    return (
        schedule, soc, active_constraints,
        expected_cost_value, expected_risk, trace,
    )


# ------------------------------------------------------------------ #
#  主入口
# ------------------------------------------------------------------ #

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
    dr_enabled: bool | None = None,
    dr_min_window_discharge_mwh: float | None = None,
    dr_min_window: tuple[int, int] | None = None,
    decision_time: pd.Timestamp | None = None,
    input_versions: Mapping[str, str] | None = None,
    config_version: str = "runtime-config",
    settlement: SettlementEngine | None = None,
    solver=None,
) -> OperationalPlan:
    """Minimize next-day real-time energy and degradation costs.

    When ``dr_enabled`` is true, runs a two-pass solve: Pass A establishes
    the baseline discharge energy in the DR window, Pass B adds DR
    participation variables (incremental discharge, binary commitment)
    and compensation to the objective.
    """
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
        if settlement is None:
            raise ValueError(
                "settlement engine is required for contract difference "
                "（v3 M4：市场规则由 MarketMode 注入）"
            )
        assert q_long is not None
        assert p_long is not None
        assert p_ref is not None
        contract_value = float(
            np.sum(
                settlement.compute_contract_difference(
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
    if scenario_set is not None and scenario_set.horizon != len(load):
        raise ValueError("scenario_set horizon must match the plan horizon")

    # ---- 确定 DR 开关 ----
    if dr_enabled is None:
        dr_enabled = config.dr.dr_enabled

    horizon = len(load)
    steps = tuple(range(horizon))
    dr_window = (config.dr.dr_window_start, config.dr.dr_window_end)
    w_start, w_end = dr_window

    # ================================================================ #
    #  无 DR 路径（含 dr_enabled=False 和 DR 窗口不在 horizon 内的情况）
    # ================================================================ #
    if not dr_enabled or w_end > horizon:
        bundle = _build_model(
            load, price, bess, config,
            contract_value=contract_value,
            scenario_set=scenario_set,
        )
        # 日内履约下限约束（Phase 3）：在无 DR 激励但需满足硬约束时
        if dr_min_window_discharge_mwh is not None:
            floor_window = dr_min_window or dr_window
            w_lo = min(floor_window[0], horizon)
            w_hi = min(floor_window[1], horizon)
            if w_hi > w_lo:
                _add_window_discharge_floor(
                    bundle.model, bundle.variables, steps,
                    window=(w_lo, w_hi), dt=config.market.dt,
                    min_mwh=dr_min_window_discharge_mwh,
                )
        schedule, soc, active_constraints, cost_val, risk_val, trace = (
            _solve_and_extract(
                bundle,
                steps=steps,
                bess=bess,
                config=config,
                contract_value=contract_value,
                decision_time=decision_time,
                input_versions=input_versions,
                config_version=config_version,
                model_tag="single-settlement-operational-v1",
                solver=solver,
            )
        )
        return OperationalPlan(
            resource_schedule=schedule,
            soc=soc,
            expected_cost=cost_val,
            expected_risk=risk_val,
            constraint_trace=active_constraints,
            decision_trace=trace,
        )

    # ================================================================ #
    #  Pass A：无 DR 激励基线模型 → 求窗口基线放电能量 Q0
    # ================================================================ #
    bundle_a = _build_model(
        load, price, bess, config,
        contract_value=contract_value,
        scenario_set=scenario_set,
    )
    if dr_min_window_discharge_mwh is not None:
        floor_window = dr_min_window or dr_window
        _add_window_discharge_floor(
            bundle_a.model, bundle_a.variables, steps,
            window=floor_window, dt=config.market.dt,
            min_mwh=dr_min_window_discharge_mwh,
        )
    schedule_a, soc_a, _, cost_a, risk_a, _ = _solve_and_extract(
        bundle_a,
        steps=steps,
        bess=bess,
        config=config,
        contract_value=contract_value,
        decision_time=decision_time,
        input_versions=input_versions,
        config_version=config_version,
        model_tag="single-settlement-operational-v1",
        solver=solver,
    )

    if config.dr.dr_baseline_mode == "fixed":
        q0 = config.dr.dr_baseline_mwh
    else:
        q0 = float(
            schedule_a["p_discharge"].iloc[w_start:w_end].sum() * config.market.dt
        )

    # ================================================================ #
    #  Pass B：同一模型追加 DR 变量/约束/补偿项 → 重解
    # ================================================================ #
    bundle_b = _build_model(
        load, price, bess, config,
        contract_value=contract_value,
        scenario_set=scenario_set,
    )
    if dr_min_window_discharge_mwh is not None:
        floor_window = dr_min_window or dr_window
        _add_window_discharge_floor(
            bundle_b.model, bundle_b.variables, steps,
            window=floor_window, dt=config.market.dt,
            min_mwh=dr_min_window_discharge_mwh,
        )

    # DR 变量
    y = LpVariable("dr_commit", cat=LpBinary)          # 是否申报
    inc = LpVariable("dr_incremental", lowBound=0.0)    # 窗口增量放电（MWh）

    # 窗口放电能量表达式
    window_discharge = lpSum(
        bundle_b.variables.p_discharge[step] * config.market.dt
        for step in steps
        if w_start <= step < w_end
    )

    # 大 M = 窗口最大可能放电能量
    window_len = w_end - w_start
    margin = config.bess.operational_power_margin
    big_m = window_len * margin * float(bess["p_bdmax"]) * config.market.dt

    # 增量 = max(0, 窗口放电 − Q0)，在 y=1 时绑定，y=0 时松弛
    # 下界：inc >= 窗口放电 − Q0 − M*(1−y)
    bundle_b.model += (
        inc >= window_discharge - q0 - big_m * (1 - y),
        "dr_incremental_lower",
    )
    # 上界：inc <= 窗口放电 − Q0 + M*(1−y)（y=1 时绑定等式）
    bundle_b.model += (
        inc <= window_discharge - q0 + big_m * (1 - y),
        "dr_incremental_upper",
    )
    # 未申报 → 增量为 0
    bundle_b.model += (
        inc <= big_m * y,
        "dr_incremental_link",
    )
    # 申报 → 必须达门槛
    bundle_b.model += (
        inc >= config.dr.dr_minimum_response_mwh * y,
        "dr_minimum_response",
    )

    # 目标函数追加补偿（负成本）：从 expected_cost 中减去
    dr_compensation = config.dr.dr_compensation_per_mwh * inc
    # 重设目标函数：原始 expected_cost - 补偿
    if scenario_set is None:
        bundle_b.model.setObjective(
            bundle_b.expected_cost - dr_compensation
        )
    else:
        # 场景模式：补偿是确定性的，只从基线分支减去
        bundle_b.model.setObjective(
            bundle_b.expected_cost
            + config.scenario.scenario_cvar_weight * bundle_b.cvar_expression
            - dr_compensation
        )

    schedule_b, soc_b, active_constraints_b, cost_b, risk_b, trace_b = (
        _solve_and_extract(
            bundle_b,
            steps=steps,
            bess=bess,
            config=config,
            contract_value=contract_value,
            decision_time=decision_time,
            input_versions=input_versions,
            config_version=config_version,
            model_tag="single-settlement-operational-v2-dr",
            solver=solver,
        )
    )

    # ---- 提取 DR 决策结果 ----
    inc_value = float(value(inc))
    y_value = float(value(y))
    participate = bool(y_value > 0.5)
    committed_qty = inc_value if participate else 0.0
    expected_compensation = config.dr.dr_compensation_per_mwh * committed_qty

    # 补充 DR 相关 trace 项
    trace_b.objective_components["dr_compensation"] = -expected_compensation
    trace_b.objective_components["dr_baseline_q0"] = q0

    dr_commitment = DRCommitment(
        committed_qty=committed_qty,
        window=dr_window,
        baseline_qty=q0,
        expected_compensation=expected_compensation,
        expected_incremental=inc_value,
        participate=participate,
        reject_reason=None if participate else (
            "insufficient incentive: "
            f"inc={inc_value:.4f} MWh below "
            f"minimum={config.dr.dr_minimum_response_mwh:.4f} MWh"
            if inc_value < config.dr.dr_minimum_response_mwh
            else "net margin below threshold"
        ),
    )

    return OperationalPlan(
        resource_schedule=schedule_b,
        soc=soc_b,
        expected_cost=cost_b,
        expected_risk=risk_b,
        constraint_trace=active_constraints_b,
        decision_trace=trace_b,
        dr_commitment=dr_commitment,
    )


def _add_window_discharge_floor(
    model: LpProblem,
    variables,
    steps: tuple[int, ...],
    *,
    window: tuple[int, int],
    dt: float,
    min_mwh: float,
) -> None:
    """添加窗口放电能量下限约束（日内履约用，Phase 3）。"""
    w_start, w_end = window
    window_steps = [step for step in steps if w_start <= step < w_end]
    if not window_steps:
        return
    model += (
        lpSum(
            variables.p_discharge[step] * dt
            for step in window_steps
        ) >= min_mwh,
        "dr_window_discharge_floor",
    )
