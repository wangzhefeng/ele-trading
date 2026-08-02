"""Walk-forward backtesting for the active single-settlement chain."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

import numpy as np
import pandas as pd

from ele_trading.domain.events import MeteringEvent, SettlementEvent
from ele_trading.operations.day_ahead_coupled import (
    solve_day_ahead_operational,
)
from ele_trading.trading.orchestrator import (
    TradingOrchestrator,
    TradingPipelineResult,
)


def _require_complete_event_chain(result: TradingPipelineResult) -> None:
    """事件链完整性断言（v3 M5）：每次回放必须有计量与结算事件。"""
    event_types = {type(event) for event in result.events}
    missing = [
        event_type.__name__
        for event_type in (MeteringEvent, SettlementEvent)
        if event_type not in event_types
    ]
    if missing:
        raise RuntimeError(
            f"incomplete event chain (v3 M5): missing {missing}"
        )


def _clone_with_risk_weight(
    orchestrator: TradingOrchestrator,
    risk_weight: float,
) -> TradingOrchestrator:
    return TradingOrchestrator(
        data_provider=orchestrator.data_provider,
        forecast_provider=orchestrator.forecast_provider,
        forecast_registry=orchestrator.forecast_registry,
        scenario_builder=orchestrator.scenario_builder,
        market_mode=orchestrator.market_mode,
        config=replace(
            orchestrator.config,
            scenario=replace(
                orchestrator.config.scenario,
                scenario_cvar_weight=float(risk_weight),
            ),
        ),
        bess=orchestrator.bess,
        config_version=orchestrator.config_version,
        solver=orchestrator.solver,
    )


def _oracle_cost(
    *,
    actual_load: np.ndarray,
    actual_price: np.ndarray,
    position,
    orchestrator: TradingOrchestrator,
    baseline_cost: float,
) -> float:
    """Use future actuals only inside the explicitly labeled oracle."""
    config = replace(
        orchestrator.config,
        scenario=replace(
            orchestrator.config.scenario,
            scenario_cvar_weight=0.0,
        ),
    )
    plan = solve_day_ahead_operational(
        actual_load,
        actual_price,
        orchestrator.bess,
        config,
        q_long=position.q_long.to_numpy(dtype=float),
        p_long=position.p_long.to_numpy(dtype=float),
        p_ref=actual_price,
        input_versions={"oracle": "future-actual"},
        config_version=orchestrator.config_version,
        settlement=orchestrator.market_mode.settlement,
        solver=orchestrator.solver,
    )
    schedule = plan.resource_schedule
    q_real = np.maximum(
        actual_load
        - schedule["p_net"].to_numpy(dtype=float) * config.market.dt,
        0.0,
    )
    degradation_cost = float(
        (schedule["p_charge"] + schedule["p_discharge"]).sum()
        * config.market.dt
        * config.bess.deg_cost_per_mwh
    )
    return orchestrator.market_mode.settlement.build_settlement_report(
        q_real=q_real,
        p_real=actual_price,
        q_long=position.q_long.to_numpy(dtype=float),
        p_long=position.p_long.to_numpy(dtype=float),
        p_ref=actual_price,
        degradation_cost=degradation_cost,
        baseline_cost=baseline_cost,
        trace=plan.decision_trace,
    ).total_cost


def run_walk_forward_backtest(
    calendar_data: Mapping[pd.Timestamp, pd.DataFrame],
    *,
    orchestrator: TradingOrchestrator,
    intraday_start: int,
    risk_aware_weight: float = 1.0,
) -> pd.DataFrame:
    """Evaluate archived vintages; future actuals are isolated to settlement/oracle."""
    if not calendar_data:
        raise ValueError("calendar_data must not be empty")
    if (
        not np.isfinite(risk_aware_weight)
        or risk_aware_weight < 0.0
    ):
        raise ValueError("risk_aware_weight must be finite and non-negative")

    deterministic = _clone_with_risk_weight(orchestrator, 0.0)
    risk_aware = _clone_with_risk_weight(
        orchestrator,
        risk_aware_weight,
    )
    rows: dict[pd.Timestamp, dict[str, object]] = {}
    for decision_time in sorted(calendar_data):
        frame = calendar_data[decision_time]
        required = {"Q_real_load", "p_real"}
        if not required.issubset(frame.columns):
            raise ValueError(
                f"daily actuals must contain columns {sorted(required)}"
            )
        actual_load = frame["Q_real_load"].to_numpy(dtype=float)
        actual_price = frame["p_real"].to_numpy(dtype=float)

        strategy_result = orchestrator.run(
            decision_time=decision_time,
            actual_load=actual_load,
            actual_price=actual_price,
            intraday_start=intraday_start,
        )
        _require_complete_event_chain(strategy_result)
        deterministic_result = deterministic.run(
            decision_time=decision_time,
            actual_load=actual_load,
            actual_price=actual_price,
            intraday_start=intraday_start,
        )
        risk_result = risk_aware.run(
            decision_time=decision_time,
            actual_load=actual_load,
            actual_price=actual_price,
            intraday_start=intraday_start,
        )
        baseline_cost = strategy_result.settlement_report.baseline_cost
        oracle_cost = _oracle_cost(
            actual_load=actual_load,
            actual_price=actual_price,
            position=strategy_result.position_state,
            orchestrator=orchestrator,
            baseline_cost=baseline_cost,
        )
        rows[pd.Timestamp(decision_time)] = {
            "strategy_cost": strategy_result.settlement_report.total_cost,
            "no_storage_cost": baseline_cost,
            "deterministic_cost": (
                deterministic_result.settlement_report.total_cost
            ),
            "risk_aware_cost": risk_result.settlement_report.total_cost,
            "oracle_cost": oracle_cost,
            "strategy_delta": (
                strategy_result.settlement_report.delta_cost
            ),
            "fallback_used": (
                strategy_result.intraday_plan.fallback_used
            ),
        }
    return pd.DataFrame.from_dict(rows, orient="index")
