"""Dependency-injected orchestration for the single-settlement trading chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from ele_trading.forecasting.contracts import (
    ForecastRequest,
    ForecastResult,
)
from ele_trading.forecasting.provider import assert_no_future_info
from ele_trading.scenario.contracts import Scenario, ScenarioSet
from ele_trading.domain.contracts import (
    IntradayPlan,
    MarketForecastBundle,
    OperationalPlan,
    PositionState,
)
from ele_trading.markets.single_settlement.contracts import (
    MarketConfig,
    SettlementReport,
)
from ele_trading.operations.day_ahead_coupled import (
    solve_day_ahead_operational,
)
from ele_trading.operations.intraday_rolling import solve_intraday_rolling
from ele_trading.markets.single_settlement.settlement import (
    build_settlement_report,
    compute_dr_settlement,
)


@dataclass(slots=True)
class TradingPipelineResult:
    """Artifacts produced by one position-to-settlement execution."""

    position_state: PositionState
    forecasts: MarketForecastBundle
    scenarios: ScenarioSet
    day_ahead_plan: OperationalPlan
    intraday_plan: IntradayPlan
    settlement_report: SettlementReport


def _period_energy(result: ForecastResult, dt: float) -> np.ndarray:
    values = result.point.to_numpy(dtype=float)
    if result.unit == "MW":
        return values * dt
    return values


def _slice_scenarios(
    scenario_set: ScenarioSet,
    start: int,
) -> ScenarioSet:
    index = scenario_set.valid_time_index[start:]
    scenarios = tuple(
        Scenario(
            scenario_id=scenario.scenario_id,
            probability=scenario.probability,
            issue_time=scenario.issue_time,
            trajectories={
                target: trajectory.iloc[start:].copy()
                for target, trajectory in scenario.trajectories.items()
            },
            seed=scenario.seed,
            source_versions=dict(scenario.source_versions),
        )
        for scenario in scenario_set.scenarios
    )
    return ScenarioSet(
        horizon=len(index),
        valid_time_index=index,
        units=dict(scenario_set.units),
        scenarios=scenarios,
        metadata=dict(scenario_set.metadata),
    )


class TradingOrchestrator:
    """Run injected data, forecast, scenario, solve and settlement stages."""

    def __init__(
        self,
        *,
        data_provider: Any,
        forecast_provider: Any,
        forecast_registry: Any,
        scenario_builder: Callable[..., ScenarioSet],
        config: MarketConfig,
        bess: Mapping[str, float],
        config_version: str,
        solver=None,
    ) -> None:
        self.data_provider = data_provider
        self.forecast_provider = forecast_provider
        self.forecast_registry = forecast_registry
        self.scenario_builder = scenario_builder
        self.config = config
        self.bess = dict(bess)
        self.config_version = config_version
        self.solver = solver

    def _forecast_bundle(
        self,
        *,
        decision_time: pd.Timestamp,
        horizon: int,
    ) -> MarketForecastBundle:
        results: dict[str, ForecastResult] = {}
        for target in ("price", "load", "wind_power", "pv_power"):
            request = ForecastRequest(
                target=target,
                scope_type="market",
                scope_id=self.config.market_name,
                horizon=horizon,
                frequency="15min",
                issue_time=decision_time,
                quantiles=(0.1, 0.9),
            )
            result = self.forecast_provider.forecast(request)
            assert_no_future_info(result, decision_time)
            results[target] = result
        return MarketForecastBundle(
            issue_time=decision_time,
            price_forecast=results["price"],
            load_forecast=results["load"],
            wind_forecast=results["wind_power"],
            pv_forecast=results["pv_power"],
        )

    def run(
        self,
        *,
        decision_time: pd.Timestamp,
        actual_load: np.ndarray,
        actual_price: np.ndarray,
        intraday_start: int,
        long_recovery: float = 0.0,
        execution_adjustment: float = 0.0,
    ) -> TradingPipelineResult:
        """Execute the active chain; actuals enter only after all decisions."""
        decision_time = pd.Timestamp(decision_time)
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        actual_load_arr = np.asarray(actual_load, dtype=float)
        actual_price_arr = np.asarray(actual_price, dtype=float)
        if (
            actual_load_arr.ndim != 1
            or not len(actual_load_arr)
            or actual_load_arr.shape != actual_price_arr.shape
            or not np.isfinite(actual_load_arr).all()
            or not np.isfinite(actual_price_arr).all()
        ):
            raise ValueError("actual load and price must be aligned vectors")
        horizon = len(actual_load_arr)
        if not 0 <= intraday_start < horizon:
            raise ValueError("intraday_start must be within the horizon")
        valid_times = pd.date_range(
            decision_time + pd.Timedelta(minutes=15),
            periods=horizon,
            freq="15min",
        )

        position = self.data_provider.get_position_state(
            decision_time,
            valid_times,
        )
        bundle = self._forecast_bundle(
            decision_time=decision_time,
            horizon=horizon,
        )
        scenarios = self.scenario_builder(
            bundle.price_forecast,
            bundle.load_forecast,
            bundle.wind_forecast,
            bundle.pv_forecast,
            num_scenarios=self.config.scenario_count,
            method=self.config.scenario_method,
            random_seed=self.config.scenario_seed,
        )
        input_versions = {
            "position": position.source_version,
            "price": bundle.price_forecast.model_version,
            "load": bundle.load_forecast.model_version,
            "wind_power": bundle.wind_forecast.model_version,
            "pv_power": bundle.pv_forecast.model_version,
            "forecast_registry": str(self.forecast_registry),
        }
        load_energy = _period_energy(
            bundle.load_forecast,
            self.config.dt,
        )
        wind_energy = _period_energy(
            bundle.wind_forecast,
            self.config.dt,
        )
        pv_energy = _period_energy(
            bundle.pv_forecast,
            self.config.dt,
        )
        net_load_forecast = np.maximum(
            load_energy - wind_energy - pv_energy,
            0.0,
        )
        price_forecast = bundle.price_forecast.point.to_numpy(dtype=float)
        q_long = position.q_long.reindex(valid_times).to_numpy(dtype=float)
        p_long = position.p_long.reindex(valid_times).to_numpy(dtype=float)
        if not np.isfinite(q_long).all() or not np.isfinite(p_long).all():
            raise ValueError("position state must align with forecast valid times")

        day_ahead = solve_day_ahead_operational(
            net_load_forecast,
            price_forecast,
            self.bess,
            self.config,
            q_long=q_long,
            p_long=p_long,
            p_ref=price_forecast,
            scenario_set=scenarios,
            decision_time=decision_time,
            input_versions=input_versions,
            config_version=self.config_version,
            solver=self.solver,
        )
        executed_prefix = day_ahead.resource_schedule.iloc[
            :intraday_start
        ].copy()

        # ---- 计算已执行窗口放电量（DR 履约核算用） ----
        dr_commitment = day_ahead.dr_commitment
        executed_window_discharge = 0.0
        if dr_commitment is not None and dr_commitment.participate:
            w_start, w_end = dr_commitment.window
            executed_in_window = min(w_end, intraday_start)
            if executed_in_window > w_start:
                executed_window_discharge = float(
                    executed_prefix["p_discharge"]
                    .iloc[w_start:executed_in_window]
                    .sum()
                    * self.config.dt
                )

        intraday = solve_intraday_rolling(
            load_forecast=net_load_forecast[intraday_start:],
            realtime_price_forecast=price_forecast[intraday_start:],
            current_soc=float(day_ahead.soc.iloc[intraday_start]),
            bess=self.bess,
            config=self.config,
            previous_plan=day_ahead,
            executed_prefix=executed_prefix,
            decision_time=decision_time,
            input_versions=input_versions,
            q_long=q_long[intraday_start:],
            p_long=p_long[intraday_start:],
            p_ref=price_forecast[intraday_start:],
            scenario_set=_slice_scenarios(scenarios, intraday_start),
            dr_commitment=dr_commitment,
            executed_window_discharge_mwh=executed_window_discharge,
            intraday_start=intraday_start,
            config_version=self.config_version,
            solver=self.solver,
        )
        executed_schedule = pd.concat(
            [
                executed_prefix.reset_index(drop=True),
                intraday.schedule.resource_schedule.reset_index(drop=True),
            ],
            ignore_index=True,
        )
        q_real = np.maximum(
            actual_load_arr
            - executed_schedule["p_net"].to_numpy(dtype=float)
            * self.config.dt,
            0.0,
        )
        baseline = build_settlement_report(
            q_real=actual_load_arr,
            p_real=actual_price_arr,
            q_long=q_long,
            p_long=p_long,
            p_ref=actual_price_arr,
        )
        degradation_cost = float(
            (
                executed_schedule["p_charge"]
                + executed_schedule["p_discharge"]
            ).sum()
            * self.config.dt
            * self.config.deg_cost_per_mwh
        )

        # ---- DR 履约结算 ----
        dr_adjustment = 0.0
        if dr_commitment is not None and dr_commitment.participate:
            w_start, w_end = dr_commitment.window
            executed_window_discharge_total = float(
                executed_schedule["p_discharge"]
                .iloc[w_start:w_end]
                .sum()
                * self.config.dt
            )
            dr_adjustment, _, _ = compute_dr_settlement(
                committed_qty=dr_commitment.committed_qty,
                executed_window_discharge_mwh=executed_window_discharge_total,
                baseline_qty=dr_commitment.baseline_qty,
                config=self.config,
            )

        settlement = build_settlement_report(
            q_real=q_real,
            p_real=actual_price_arr,
            q_long=q_long,
            p_long=p_long,
            p_ref=actual_price_arr,
            long_recovery=long_recovery,
            dr_adjustment=dr_adjustment,
            degradation_cost=degradation_cost,
            execution_adjustment=execution_adjustment,
            baseline_cost=baseline.total_cost,
            trace=intraday.schedule.decision_trace,
        )
        return TradingPipelineResult(
            position_state=position,
            forecasts=bundle,
            scenarios=scenarios,
            day_ahead_plan=day_ahead,
            intraday_plan=intraday,
            settlement_report=settlement,
        )
