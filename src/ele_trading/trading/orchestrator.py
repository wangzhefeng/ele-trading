"""Dependency-injected orchestration for the single-settlement trading chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, cast

import numpy as np
import pandas as pd

from ele_trading.forecasting.contracts import (
    ForecastRequest,
    ForecastResult,
)
from ele_trading.forecasting.provider import assert_no_future_info
from ele_trading.scenario.contracts import ScenarioSet
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
from ele_trading.domain.price_roles import (
    PriceRole,
    legacy_price_scope,
    normalize_price_role,
)
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
    intraday_forecasts: MarketForecastBundle | None = None
    intraday_scenarios: ScenarioSet | None = None


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


class TradingOrchestrator:
    """Run injected data, forecast, scenario, solve and settlement stages."""

    def __init__(
        self,
        *,
        data_provider: Any,
        forecast_provider: Any,
        forecast_registry: Any,
        scenario_builder: Any,
        market_mode: MarketMode,
        config: MarketConfig,
        bess: Mapping[str, float],
        config_version: str,
        solver=None,
        market_state_provider: Any | None = None,
        extreme_templates: tuple[Any, ...] = (),
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
        self.market_state_provider = market_state_provider
        self.extreme_templates = tuple(extreme_templates)

    def _forecast_bundle(
        self,
        *,
        decision_time: pd.Timestamp,
        horizon: int,
        price_roles: tuple[str, ...],
        primary_price_role: str,
    ) -> MarketForecastBundle:
        normalized_roles = tuple(
            normalize_price_role(role).value for role in price_roles
        )
        primary_price_role = normalize_price_role(primary_price_role).value
        if primary_price_role not in normalized_roles:
            raise ValueError("primary price role must be included in price_roles")
        price_results: dict[str, ForecastResult] = {}
        for price_role in normalized_roles:
            request = ForecastRequest(
                target="price",
                scope_type="market",
                scope_id=self.config.market.market_name,
                horizon=horizon,
                frequency="15min",
                issue_time=decision_time,
                quantiles=(0.1, 0.9),
                data={
                    "price_role": price_role,
                    "market_scope": legacy_price_scope(price_role),
                    "rule_version": self.config_version,
                },
            )
            result = self.forecast_provider.forecast(request)
            assert_no_future_info(result, decision_time)
            price_results[price_role] = result

        results: dict[str, ForecastResult] = {}
        for target in ("load", "wind_power", "pv_power"):
            request = ForecastRequest(
                target=target,
                scope_type="market",
                scope_id=self.config.market.market_name,
                horizon=horizon,
                frequency="15min",
                issue_time=decision_time,
                quantiles=(0.1, 0.9),
                data={"rule_version": self.config_version},
            )
            result = self.forecast_provider.forecast(request)
            assert_no_future_info(result, decision_time)
            results[target] = result

        market_state_forecast = None
        if self.market_state_provider is not None:
            feature_getter = getattr(
                self.data_provider,
                "get_market_state_features",
                None,
            )
            if not callable(feature_getter):
                raise ValueError(
                    "data_provider must expose get_market_state_features "
                    "when market_state_provider is configured"
                )
            valid_time_index = results["load"].point.index
            feature_snapshot = feature_getter(
                decision_time,
                valid_time_index,
            )
            market_state_forecast = self.market_state_provider.forecast(
                decision_time,
                feature_snapshot,
            )
            if not market_state_forecast.valid_time_index.equals(
                valid_time_index
            ):
                raise ValueError(
                    "market-state forecast must align with forecast horizon"
                )
        return MarketForecastBundle(
            issue_time=decision_time,
            price_forecast=price_results[primary_price_role],
            load_forecast=results["load"],
            wind_forecast=results["wind_power"],
            pv_forecast=results["pv_power"],
            price_forecasts=price_results,
            market_state_forecast=market_state_forecast,
        )

    @staticmethod
    def _append_forecast_events(
        events: list[TradingEvent],
        *,
        bundle: MarketForecastBundle,
        calendar: MarketCalendar,
        stage: str,
    ) -> None:
        for price_role, forecast_result in bundle.price_forecasts.items():
            if stage == "day_ahead" and forecast_result is bundle.price_forecast:
                source = "price"
            else:
                suffix = "" if stage == "day_ahead" else f":{stage}"
                source = f"price:{price_role}{suffix}"
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
                    source=source,
                    calendar=calendar,
                    unit=str(forecast_result.unit),
                )
            )
        for target, forecast_result in (
            ("load", bundle.load_forecast),
            ("wind_power", bundle.wind_forecast),
            ("pv_power", bundle.pv_forecast),
        ):
            suffix = "" if stage == "day_ahead" else f":{stage}"
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
                    source=f"{target}{suffix}",
                    calendar=calendar,
                    unit=str(forecast_result.unit),
                )
            )
        state = bundle.market_state_forecast
        if state is not None:
            suffix = "" if stage == "day_ahead" else f":{stage}"
            events.append(
                ForecastEvent(
                    issue_time=state.issue_time,
                    valid_time=cast(
                        pd.Timestamp,
                        state.valid_time_index[0],
                    ),
                    version=state.model_version,
                    source=f"market_state{suffix}",
                    calendar=calendar,
                    unit="probability",
                )
            )

    def _build_scenarios(
        self,
        bundle: MarketForecastBundle,
        *,
        seed_offset: int,
    ) -> ScenarioSet:
        """按 builder 能力选择 v3 联合场景或 v5 状态条件场景。"""
        random_seed = self.config.scenario.scenario_seed + seed_offset
        if getattr(self.scenario_builder, "supports_market_state", False):
            build_from_bundle = getattr(
                self.scenario_builder,
                "build_from_bundle",
                None,
            )
            if not callable(build_from_bundle):
                raise ValueError(
                    "state-aware scenario builder must implement build_from_bundle"
                )
            scenario_set = build_from_bundle(
                bundle,
                num_scenarios=self.config.scenario.scenario_count,
                random_seed=random_seed,
                extreme_templates=self.extreme_templates,
            )
            if not isinstance(scenario_set, ScenarioSet):
                raise ValueError("scenario builder must return ScenarioSet")
            return scenario_set
        scenario_set = self.scenario_builder(
            bundle.price_forecast,
            bundle.load_forecast,
            bundle.wind_forecast,
            bundle.pv_forecast,
            num_scenarios=self.config.scenario.scenario_count,
            method=self.config.scenario.scenario_method,
            random_seed=random_seed,
        )
        if not isinstance(scenario_set, ScenarioSet):
            raise ValueError("scenario builder must return ScenarioSet")
        return scenario_set

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
        mode_price_roles = tuple(
            getattr(
                self.market_mode,
                "price_roles",
                (PriceRole.REAL_TIME_SETTLEMENT.value,),
            )
        )
        day_ahead_price_role = str(
            getattr(
                self.market_mode,
                "day_ahead_price_role",
                PriceRole.REAL_TIME_SETTLEMENT.value,
            )
        )
        intraday_price_role = str(
            getattr(
                self.market_mode,
                "intraday_price_role",
                PriceRole.REAL_TIME_SETTLEMENT.value,
            )
        )
        bundle = self._forecast_bundle(
            decision_time=decision_time,
            horizon=horizon,
            price_roles=mode_price_roles,
            primary_price_role=day_ahead_price_role,
        )
        scenarios = self._build_scenarios(bundle, seed_offset=0)
        # ---- 事件链（v3 M5）：每个决策 vintage 独立派生 input_versions ----
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
        self._append_forecast_events(
            events,
            bundle=bundle,
            calendar=calendar,
            stage="day_ahead",
        )
        day_ahead_input_versions = derive_input_versions(
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
            input_versions=day_ahead_input_versions,
            config_version=self.config_version,
            settlement=self.market_mode.settlement,
            solver=self.solver,
        )
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

        intraday_decision_time = cast(
            pd.Timestamp,
            decision_time
            + pd.Timedelta(hours=self.config.market.dt * intraday_start),
        )
        intraday_forecasts = self._forecast_bundle(
            decision_time=intraday_decision_time,
            horizon=horizon - intraday_start,
            price_roles=(intraday_price_role,),
            primary_price_role=intraday_price_role,
        )
        intraday_scenarios = self._build_scenarios(
            intraday_forecasts,
            seed_offset=1,
        )
        self._append_forecast_events(
            events,
            bundle=intraday_forecasts,
            calendar=calendar,
            stage="intraday",
        )
        intraday_input_versions = derive_input_versions(
            events,
            extra={"forecast_registry": str(self.forecast_registry)},
        )
        intraday_load_energy = _period_energy(
            intraday_forecasts.load_forecast,
            self.config.market.dt,
        )
        intraday_wind_energy = _period_energy(
            intraday_forecasts.wind_forecast,
            self.config.market.dt,
        )
        intraday_pv_energy = _period_energy(
            intraday_forecasts.pv_forecast,
            self.config.market.dt,
        )
        intraday_net_load = np.maximum(
            intraday_load_energy
            - intraday_wind_energy
            - intraday_pv_energy,
            0.0,
        )
        intraday_price_forecast = (
            intraday_forecasts.price_forecast.point.to_numpy(dtype=float)
        )
        intraday = solve_intraday_rolling(
            load_forecast=intraday_net_load,
            realtime_price_forecast=intraday_price_forecast,
            current_soc=float(day_ahead.soc.iloc[intraday_start]),
            bess=self.bess,
            config=self.config,
            previous_plan=day_ahead,
            executed_prefix=executed_prefix,
            decision_time=intraday_decision_time,
            input_versions=intraday_input_versions,
            q_long=q_long[intraday_start:],
            p_long=p_long[intraday_start:],
            p_ref=intraday_price_forecast,
            scenario_set=intraday_scenarios,
            dr_commitment=dr_commitment,
            executed_window_discharge_mwh=executed_window_discharge,
            intraday_start=intraday_start,
            config_version=self.config_version,
            settlement=self.market_mode.settlement,
            solver=self.solver,
        )
        events.append(
            DispatchEvent(
                issue_time=intraday_decision_time,
                valid_time=cast(pd.Timestamp, valid_times[intraday_start]),
                version=_dispatch_model_tag(intraday.schedule),
                source="dispatch:intraday",
                calendar=calendar,
                unit="MW",
            )
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
        # ---- 事件链收尾（v3 M5）：实际量测 → 结算 ----
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
            intraday_forecasts=intraday_forecasts,
            intraday_scenarios=intraday_scenarios,
        )
