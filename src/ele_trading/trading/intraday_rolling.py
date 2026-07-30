"""Rolling single-settlement physical rescheduling."""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from ele_trading.scenario.contracts import ScenarioSet
from ele_trading.trading.contracts import (
    DecisionTrace,
    DRCommitment,
    IntradayAdjustment,
    IntradayPlan,
    MarketConfig,
    OperationalPlan,
)
from ele_trading.trading.day_ahead_coupled import (
    solve_day_ahead_operational,
)
from ele_trading.trading.settlement_mengxi import (
    compute_contract_difference,
)


def _remaining_previous_schedule(
    previous_plan: OperationalPlan,
    executed_count: int,
    remaining_horizon: int,
) -> pd.DataFrame:
    schedule = previous_plan.resource_schedule
    if len(schedule) == executed_count + remaining_horizon:
        remaining = schedule.iloc[executed_count:]
    elif len(schedule) == remaining_horizon:
        remaining = schedule
    else:
        raise ValueError(
            "previous plan must cover the full or remaining rolling horizon"
        )
    return remaining.reset_index(drop=True).copy()


def _clip_fallback(
    previous_remaining: pd.DataFrame,
    *,
    load_forecast: np.ndarray,
    price_forecast: np.ndarray,
    current_soc: float,
    bess: Mapping[str, float],
    config: MarketConfig,
    decision_time: pd.Timestamp | None,
    reason: str,
    q_long: np.ndarray | None,
    p_long: np.ndarray | None,
    p_ref: np.ndarray | None,
) -> OperationalPlan:
    p_charge: list[float] = []
    p_discharge: list[float] = []
    soc_values = [float(np.clip(
        current_soc,
        float(bess["socmin"]),
        float(bess["socmax"]),
    ))]
    for step, previous_net in enumerate(
        previous_remaining["p_net"].to_numpy(dtype=float)
    ):
        soc = soc_values[-1]
        if previous_net < 0.0:
            max_by_soc = (
                (float(bess["socmax"]) - soc)
                / (float(bess["p_bceff"]) * config.dt)
            )
            charge = min(
                -previous_net,
                float(bess["p_bcmax"]),
                max(0.0, max_by_soc),
            )
            discharge = 0.0
        else:
            max_by_soc = (
                (soc - float(bess["socmin"]))
                * float(bess["p_bdeff"])
                / config.dt
            )
            max_by_load = max(0.0, float(load_forecast[step]) / config.dt)
            charge = 0.0
            discharge = min(
                previous_net,
                float(bess["p_bdmax"]),
                max_by_soc,
                max_by_load,
            )
        next_soc = (
            soc
            + float(bess["p_bceff"]) * charge * config.dt
            - discharge * config.dt / float(bess["p_bdeff"])
        )
        p_charge.append(charge)
        p_discharge.append(discharge)
        soc_values.append(
            float(
                np.clip(
                    next_soc,
                    float(bess["socmin"]),
                    float(bess["socmax"]),
                )
            )
        )

    schedule = pd.DataFrame(
        {
            "p_charge": p_charge,
            "p_discharge": p_discharge,
            "p_net": np.asarray(p_discharge) - np.asarray(p_charge),
        }
    )
    energy_cost = float(
        np.sum(
            (
                load_forecast
                - schedule["p_net"].to_numpy(dtype=float) * config.dt
            )
            * price_forecast
        )
    )
    degradation_cost = float(
        np.sum(
            (
                schedule["p_charge"].to_numpy(dtype=float)
                + schedule["p_discharge"].to_numpy(dtype=float)
            )
            * config.dt
            * config.deg_cost_per_mwh
        )
    )
    if q_long is None:
        contract_difference = 0.0
    else:
        assert p_long is not None
        assert p_ref is not None
        contract_difference = float(
            np.sum(
                compute_contract_difference(
                    q_long,
                    p_long,
                    p_ref=p_ref,
                )
            )
        )
    trace = DecisionTrace(
        decision_time=decision_time or pd.Timestamp.now(tz="UTC"),
        input_versions={},
        model_versions={"dispatch": "single-settlement-intraday-v1"},
        config_version="runtime-config",
        solver_name="fallback",
        solver_version="n/a",
        solver_status="fallback",
        objective_components={
            "energy_cost": energy_cost,
            "degradation_cost": degradation_cost,
            "contract_difference": contract_difference,
        },
        active_constraints={},
        fallback_used=True,
        fallback_reason=reason,
    )
    return OperationalPlan(
        resource_schedule=schedule,
        soc=pd.Series(soc_values, name="soc"),
        expected_cost=(
            energy_cost
            + degradation_cost
            + contract_difference
        ),
        expected_risk=0.0,
        constraint_trace={},
        decision_trace=trace,
    )


def solve_intraday_rolling(
    *,
    load_forecast: np.ndarray,
    realtime_price_forecast: np.ndarray,
    current_soc: float,
    bess: Mapping[str, float],
    config: MarketConfig,
    previous_plan: OperationalPlan,
    executed_prefix: pd.DataFrame,
    decision_time: pd.Timestamp | None = None,
    input_versions: Mapping[str, str] | None = None,
    q_long: np.ndarray | None = None,
    p_long: np.ndarray | None = None,
    p_ref: np.ndarray | None = None,
    scenario_set: ScenarioSet | None = None,
    dr_commitment: DRCommitment | None = None,
    executed_window_discharge_mwh: float = 0.0,
    intraday_start: int = 0,
    config_version: str = "runtime-config",
    solver=None,
) -> IntradayPlan:
    """Freeze execution and optimize only the remaining physical schedule.

    When ``dr_commitment`` is provided and the remaining horizon overlaps
    the DR window, a discharge floor constraint is passed to the day-ahead
    solver to enforce fulfillment of the remaining commitment.
    """
    load = np.asarray(load_forecast, dtype=float)
    price = np.asarray(realtime_price_forecast, dtype=float)
    if (
        load.ndim != 1
        or price.shape != load.shape
        or not len(load)
        or not np.isfinite(load).all()
        or not np.isfinite(price).all()
    ):
        raise ValueError(
            "remaining load and price forecasts must be aligned finite vectors"
        )
    frozen_prefix = executed_prefix.copy(deep=True)
    previous_remaining = _remaining_previous_schedule(
        previous_plan,
        len(frozen_prefix),
        len(load),
    )
    bess_current = {**bess, "socini": float(current_soc)}

    # ---- 计算日内履约下限 ----
    dr_min_discharge: float | None = None
    dr_min_window_rel: tuple[int, int] | None = None
    if (
        dr_commitment is not None
        and dr_commitment.participate
        and dr_commitment.committed_qty > 0.0
    ):
        # 剩余需履约量 = 申报量 − 已执行窗口放电量
        remaining_commitment = max(
            0.0,
            dr_commitment.committed_qty - executed_window_discharge_mwh,
        )
        if remaining_commitment > 1e-9:
            # DR 窗口在剩余 horizon 内的相对位置
            w_start_rel = dr_commitment.window[0] - intraday_start
            w_end_rel = dr_commitment.window[1] - intraday_start
            # 窗口与剩余 horizon 有交集
            if w_end_rel > 0 and w_start_rel < len(load):
                dr_min_discharge = remaining_commitment
                dr_min_window_rel = (
                    max(0, w_start_rel),
                    min(len(load), w_end_rel),
                )

    try:
        schedule = solve_day_ahead_operational(
            load,
            price,
            bess_current,
            config,
            q_long=q_long,
            p_long=p_long,
            p_ref=p_ref,
            scenario_set=scenario_set,
            dr_enabled=False,
            dr_min_window_discharge_mwh=dr_min_discharge,
            dr_min_window=dr_min_window_rel,
            decision_time=decision_time,
            input_versions=input_versions,
            config_version=config_version,
            solver=solver,
        )
        fallback_used = False
    except RuntimeError as exc:
        schedule = _clip_fallback(
            previous_remaining,
            load_forecast=load,
            price_forecast=price,
            current_soc=current_soc,
            bess=bess,
            config=config,
            decision_time=decision_time,
            reason=str(exc),
            q_long=q_long,
            p_long=p_long,
            p_ref=p_ref,
        )
        fallback_used = True

    previous_net = previous_remaining["p_net"].reset_index(drop=True)
    new_net = schedule.resource_schedule["p_net"].reset_index(drop=True)
    delta = new_net - previous_net
    reasons: list[str] = []
    if fallback_used:
        reasons.append("solve_failure")
    elif not np.allclose(delta, 0.0, atol=1e-9, rtol=0.0):
        reasons.append("forecast_update")
    adjustment = IntradayAdjustment(
        p_net_new=new_net,
        delta_p_net=delta,
        expected_cost_delta=(
            schedule.expected_cost - previous_plan.expected_cost
        ),
        reasons=tuple(reasons),
    )
    return IntradayPlan(
        schedule=schedule,
        executed_prefix=frozen_prefix,
        adjustment=adjustment,
        fallback_used=fallback_used,
    )
