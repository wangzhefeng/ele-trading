"""Dependency-injected orchestration for the single-settlement trading chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, cast

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
from ele_trading.domain.events import (
    AwardEvent,
    DispatchEvent,
    ForecastEvent,
    MarketCalendar,
    MeteringEvent,
    SettlementEvent,
    TradingEvent,
    derive_input_versions,
)
from ele_trading.markets.protocol import MarketMode
from ele_trading.markets.sections import MarketConfig
from ele_trading.operations.day_ahead_coupled import (
    solve_day_ahead_operational,
)
from ele_trading.operations.intraday_rolling import solve_intraday_rolling


@dataclass(slots=True)
class TradingPipelineResult:
    """Artifacts produced by one position-to-settlement execution."""

    position_state: PositionState
    forecasts: MarketForecastBundle
    scenarios: ScenarioSet
    day_ahead_plan: OperationalPlan
    intraday_plan: IntradayPlan
    settlement_report: Any  # 报告类型由市场模式定义（v3 M4）
    events: tuple[TradingEvent, ...]  # 事件链（v3 M5 / D-006）


def _dispatch_model_tag(plan: OperationalPlan) -> str:
    """从计划的 DecisionTrace 取 dispatch 模型版本（trace 缺失时 unknown）。"""
    trace = plan.decision_trace
    if trace is None:
        return "unknown"
    return str(trace.model_versions.get("dispatch", "unknown"))


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
        market_mode: MarketMode,
        config: MarketConfig,
        bess: Mapping[str, float],
        config_version: str,
        solver=None,
    ) -> None:
        self.data_provider = data_provider
        self.forecast_provider = forecast_provider
        self.forecast_registry = forecast_registry
        self.scenario_builder = scenario_builder
        self.market_mode = market_mode
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
                scope_id=self.config.market.market_name,
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
            num_scenarios=self.config.scenario.scenario_count,
            method=self.config.scenario.scenario_method,
            random_seed=self.config.scenario.scenario_seed,
        )
        # ---- 事件链（v3 M5）：Award + Forecast 事件先建，
        #      input_versions 由事件链唯一派生 ----
        calendar = MarketCalendar(
            market=self.config.market.market_name,
            tz=str(decision_time.tz),
            freq_minutes=int(round(self.config.market.dt * 60)),
            settle_periods=self.config.market.settle_periods,
        )
        events: list[TradingEvent] = []
        events.append(
            AwardEvent(
                issue_time=decision_time,
                valid_time=cast(pd.Timestamp, valid_times[0]),
                version=position.source_version,
                source="position",
                calendar=calendar,
                unit="MWh",
            )
        )
        for target, forecast_result in (
            ("price", bundle.price_forecast),
            ("load", bundle.load_forecast),
            ("wind_power", bundle.wind_forecast),
            ("pv_power", bundle.pv_forecast),
        ):
            events.append(
                ForecastEvent(
                    issue_time=cast(
                        pd.Timestamp,
                        pd.Timestamp(forecast_result.request.issue_time),
                    ),
                    valid_time=cast(
                        pd.Timestamp,
                        pd.Timestamp(forecast_result.point.index[0]),
                    ),
                    version=forecast_result.model_version,
                    source=target,
                    calendar=calendar,
                    unit=str(forecast_result.unit),
                )
            )
        input_versions = derive_input_versions(
            events,
            extra={"forecast_registry": str(self.forecast_registry)},
        )
        load_energy = _period_energy(
            bundle.load_forecast,
            self.config.market.dt,
        )
        wind_energy = _period_energy(
            bundle.wind_forecast,
            self.config.market.dt,
        )
        pv_energy = _period_energy(
            bundle.pv_forecast,
            self.config.market.dt,
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
            settlement=self.market_mode.settlement,
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
                    * self.config.market.dt
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
            settlement=self.market_mode.settlement,
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
            * self.config.market.dt,
            0.0,
        )
        baseline = self.market_mode.settlement.build_settlement_report(
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
            * self.config.market.dt
            * self.config.bess.deg_cost_per_mwh
        )

        # ---- DR 履约结算 ----
        dr_adjustment = 0.0
        if dr_commitment is not None and dr_commitment.participate:
            w_start, w_end = dr_commitment.window
            executed_window_discharge_total = float(
                executed_schedule["p_discharge"]
                .iloc[w_start:w_end]
                .sum()
                * self.config.market.dt
            )
            dr_adjustment, _, _ = (
                self.market_mode.settlement.compute_dr_settlement(
                    committed_qty=dr_commitment.committed_qty,
                    executed_window_discharge_mwh=executed_window_discharge_total,
                    baseline_qty=dr_commitment.baseline_qty,
                    config=self.config,
                )
            )

        settlement = self.market_mode.settlement.build_settlement_report(
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
        # ---- 事件链收尾（v3 M5）：Dispatch → Metering → Settlement ----
        events.append(
            DispatchEvent(
                issue_time=decision_time,
                valid_time=cast(pd.Timestamp, valid_times[0]),
                version=_dispatch_model_tag(day_ahead),
                source="dispatch:day_ahead",
                calendar=calendar,
                unit="MW",
            )
        )
        events.append(
            DispatchEvent(
                issue_time=decision_time,
                valid_time=cast(pd.Timestamp, valid_times[intraday_start]),
                version=_dispatch_model_tag(intraday.schedule),
                source="dispatch:intraday",
                calendar=calendar,
                unit="MW",
            )
        )
        events.append(
            MeteringEvent(
                issue_time=decision_time,
                valid_time=cast(pd.Timestamp, valid_times[0]),
                version=f"actuals:{decision_time.date()}",
                source="metering",
                calendar=calendar,
                unit="MWh",
            )
        )
        events.append(
            SettlementEvent(
                issue_time=decision_time,
                valid_time=cast(pd.Timestamp, valid_times[0]),
                version=self.config_version,
                source=f"settlement:{self.market_mode.name}",
                calendar=calendar,
                unit="CNY",
            )
        )
        return TradingPipelineResult(
            position_state=position,
            forecasts=bundle,
            scenarios=scenarios,
            day_ahead_plan=day_ahead,
            intraday_plan=intraday,
            settlement_report=settlement,
            events=tuple(events),
        )
