"""Dependency-injected orchestration for the single-settlement trading chain."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, cast

import numpy as np
import pandas as pd

from ele_trading.forecasting.contracts import (
    ForecastRequest,
    ForecastResult,
)
from ele_trading.forecasting.provider import assert_no_future_info
from ele_trading.scenario.contracts import ScenarioSet
from ele_trading.scenario.diagnostics import diagnose_scenario_set
from ele_trading.domain.contracts import (
    AwardFulfillment,
    AwardedCommitment,
    BidSubmission,
    BillingStatement,
    ContractType,
    IntradayPlan,
    MarketAwardReceipt,
    MarketForecastBundle,
    OperationalPlan,
    PositionState,
    ResourceExecutionDeviation,
    ResourceMetering,
    match_award_receipt,
)
from ele_trading.domain.events import (
    AwardEvent,
    BidEvent,
    DispatchEvent,
    ForecastEvent,
    MarketCalendar,
    MeteringEvent,
    PositionEvent,
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
from ele_trading.operations.execution_bias import ExecutionBiasEstimator
from ele_trading.operations.intraday_rolling import solve_intraday_rolling
from ele_trading.operations.multi_resource import (
    DemandResponseUnit,
    MultiResourcePortfolio,
    MultiResourceResult,
    RenewableUnit,
    solve_multi_resource,
)
from ele_trading.operations.multi_resource_intraday import (
    MultiResourceIntradayPlan,
    solve_multi_resource_intraday,
)
from ele_trading.operations.resource_runtime import (
    ResourceActual,
    ResourceOperationalPlan,
)
from ele_trading.trading.scenario_admission import (
    ScenarioAdmissionDecision,
    ScenarioAdmissionPolicy,
    ScenarioAdmissionRejected,
    ScenarioEvidenceTier,
)


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
    reconciliation_report: Any | None = None  # 结算后对账（V5-9）
    intraday_forecasts: MarketForecastBundle | None = None
    intraday_scenarios: ScenarioSet | None = None
    multi_resource_result: MultiResourceResult | None = None
    resource_operational_plan: ResourceOperationalPlan | None = None
    multi_resource_intraday_plan: MultiResourceIntradayPlan | None = None
    award_fulfillments: tuple[AwardFulfillment, ...] = ()
    resource_execution_deviations: tuple[ResourceExecutionDeviation, ...] = ()
    scenario_admissions: tuple[ScenarioAdmissionDecision, ...] = ()


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
        execution_bias_estimator: ExecutionBiasEstimator | None = None,
        multi_resource_portfolio: MultiResourcePortfolio | None = None,
        bid_candidate_builder: Any | None = None,
        scenario_admission_policy: ScenarioAdmissionPolicy | None = None,
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
        self.execution_bias_estimator = execution_bias_estimator
        self.multi_resource_portfolio = multi_resource_portfolio
        self.bid_candidate_builder = bid_candidate_builder
        self.scenario_admission_policy = (
            scenario_admission_policy
            if scenario_admission_policy is not None
            else ScenarioAdmissionPolicy(
                evidence_tier=ScenarioEvidenceTier.RESEARCH,
            )
        )

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

    @staticmethod
    def _scenario_reference(
        bundle: MarketForecastBundle,
        scenario_set: ScenarioSet,
    ) -> dict[str, pd.Series]:
        """从当前 forecast vintage 构造场景诊断的中心参考。"""
        references = {
            "price": bundle.price_forecast.point,
            "load": bundle.load_forecast.point,
            "wind_power": bundle.wind_forecast.point,
            "pv_power": bundle.pv_forecast.point,
        }
        day_ahead_price = bundle.price_forecasts.get(
            PriceRole.DAY_AHEAD_SETTLEMENT.value
        ) or bundle.price_forecasts.get(PriceRole.DAY_AHEAD_REFERENCE.value)
        if day_ahead_price is not None:
            references["day_ahead_price"] = day_ahead_price.point
        real_time_price = bundle.price_forecasts.get(
            PriceRole.REAL_TIME_SETTLEMENT.value
        )
        if real_time_price is not None:
            references["real_time_price"] = real_time_price.point
        missing = set(scenario_set.units) - set(references)
        if missing:
            raise ValueError(
                "scenario targets have no forecast reference: "
                f"{sorted(missing)!r}"
            )
        return {
            target: references[target]
            for target in scenario_set.units
        }

    def _admit_scenarios(
        self,
        *,
        stage: str,
        bundle: MarketForecastBundle,
        scenario_set: ScenarioSet,
    ) -> ScenarioAdmissionDecision:
        """诊断并强制执行当前证据层级的场景准入规则。"""
        diagnostics = diagnose_scenario_set(
            scenario_set,
            reference=self._scenario_reference(bundle, scenario_set),
        )
        decision = self.scenario_admission_policy.evaluate(diagnostics).for_stage(
            stage
        )
        if not decision.admitted:
            raise ScenarioAdmissionRejected(decision)
        return decision

    @staticmethod
    def _record_scenario_admission(
        plan: OperationalPlan,
        decision: ScenarioAdmissionDecision,
    ) -> None:
        """将准入状态写入决策 trace，供重放和晋级门审计。"""
        trace = plan.decision_trace
        if trace is None:
            return
        assert decision.stage is not None
        prefix = f"scenario_admission.{decision.stage}"
        trace.diagnostics = {
            **trace.diagnostics,
            f"{prefix}.status": decision.status.value,
            f"{prefix}.evidence_tier": decision.evidence_tier.value,
            f"{prefix}.failed_checks": ",".join(decision.failed_checks),
            f"{prefix}.degraded_checks": ",".join(
                decision.degraded_checks
            ),
        }

    def run(
        self,
        *,
        decision_time: pd.Timestamp,
        actual_load: np.ndarray,
        actual_price: np.ndarray,
        intraday_start: int,
        long_recovery: float = 0.0,
        execution_adjustment: float = 0.0,
        award_receipts: tuple[MarketAwardReceipt, ...] = (),
        dispatch_decision_time: pd.Timestamp | None = None,
        resource_metering: ResourceMetering | None = None,
        resource_meterings: tuple[ResourceMetering, ...] = (),
        multi_resource_actual_soc_mwh: Mapping[str, float] | None = None,
        multi_resource_actuals: Mapping[str, ResourceActual] | None = None,
        billing_statement: BillingStatement | None = None,
    ) -> TradingPipelineResult:
        """Execute the active chain; actuals enter only after all decisions."""
        decision_time = pd.Timestamp(decision_time)
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        dispatch_time = pd.Timestamp(
            dispatch_decision_time
            if dispatch_decision_time is not None
            else decision_time
        )
        if dispatch_time.tzinfo is None:
            raise ValueError("dispatch_decision_time must be timezone-aware")
        if dispatch_time < decision_time:
            raise ValueError("dispatch_decision_time cannot be earlier than decision_time")
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
        if dispatch_time > valid_times[0]:
            raise ValueError(
                "dispatch_decision_time must be no later than first delivery period"
            )

        position = self.data_provider.get_position_state(
            decision_time,
            valid_times,
        )
        if position.contract_type is not ContractType.FINANCIAL_DIFFERENCE:
            raise ValueError(
                f"{position.contract_type.value} requires a confirmed "
                "MarketProfile commitment projection and settlement policy"
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
        day_ahead_admission = self._admit_scenarios(
            stage="day_ahead",
            bundle=bundle,
            scenario_set=scenarios,
        )
        # ---- 事件链（v3 M5）：每个决策 vintage 独立派生 input_versions ----
        calendar = MarketCalendar(
            market=self.config.market.market_name,
            tz=str(decision_time.tz),
            freq_minutes=int(round(self.config.market.dt * 60)),
            settle_periods=self.config.market.settle_periods,
        )
        events: list[TradingEvent] = []
        events.append(
            PositionEvent(
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

        provisional_day_ahead = solve_day_ahead_operational(
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
        self._record_scenario_admission(
            provisional_day_ahead,
            day_ahead_admission,
        )

        # ---- V5-8：候选报价 → capability 验证 → Bid；Award 仅来自市场回执 ----
        submitted_bids: dict[str, BidSubmission] = {}
        bid_capability = getattr(
            self.market_mode,
            "bid_submission_capability",
            None,
        )
        if bid_capability is not None and self.bid_candidate_builder is not None:
            candidate = self.bid_candidate_builder(
                decision_time=decision_time,
                valid_times=valid_times,
                plan=provisional_day_ahead,
                bundle=bundle,
                config_version=self.config_version,
            )
            if not isinstance(candidate, BidSubmission):
                raise ValueError(
                    "bid_candidate_builder must return a BidSubmission"
                )
            submission_decision = bid_capability.validate_submission(candidate)
            trace = provisional_day_ahead.decision_trace
            if submission_decision.accepted:
                events.append(
                    BidEvent(
                        issue_time=decision_time,
                        valid_time=candidate.delivery_start,
                        version=candidate.strategy_version,
                        source=f"bid:{candidate.product}",
                        calendar=calendar,
                        unit="MWh",
                        bid_id=candidate.bid_id,
                    )
                )
                submitted_bids[candidate.bid_id] = candidate
                if trace is not None:
                    trace.diagnostics = {
                        **trace.diagnostics,
                        "bid.submitted": "true",
                    }
            else:
                if trace is not None:
                    trace.diagnostics = {
                        **trace.diagnostics,
                        "bid.submitted": "false",
                        "bid.rejected_reason": str(
                            submission_decision.reason
                        ),
                    }
        awarded_commitments: list[AwardedCommitment] = []
        late_award_events: list[AwardEvent] = []
        late_awarded_commitments: list[tuple[pd.Timestamp, AwardedCommitment]] = []
        already_awarded_by_bid: dict[str, float] = {}
        for receipt in award_receipts:
            award_event = AwardEvent(
                issue_time=receipt.receipt_time,
                valid_time=receipt.delivery_start,
                version=receipt.source_version,
                source="award:market",
                calendar=calendar,
                unit="MWh",
                award_id=receipt.award_id,
                bid_id=receipt.bid_id,
                external_award_reference=receipt.external_award_reference,
            )
            if receipt.bid_id is None:
                if receipt.receipt_time <= dispatch_time:
                    events.append(award_event)
                else:
                    late_award_events.append(award_event)
                continue
            try:
                bid = submitted_bids[receipt.bid_id]
            except KeyError as exc:
                raise ValueError(
                    f"award receipt references unknown bid: {receipt.bid_id!r}"
                ) from exc
            matched_award = match_award_receipt(
                receipt=receipt,
                bid=bid,
                already_awarded_mwh=already_awarded_by_bid.get(receipt.bid_id, 0.0),
            )
            already_awarded_by_bid[receipt.bid_id] = (
                already_awarded_by_bid.get(receipt.bid_id, 0.0)
                + receipt.cleared_quantity_mwh
            )
            if receipt.receipt_time <= dispatch_time:
                events.append(award_event)
                awarded_commitments.append(
                    AwardedCommitment.from_matched_award(
                        matched_award,
                        valid_times=valid_times,
                        dt_hours=self.config.market.dt,
                    )
                )
            else:
                late_award_events.append(award_event)
                late_awarded_commitments.append(
                    (
                        receipt.receipt_time,
                        AwardedCommitment.from_matched_award(
                            matched_award,
                            valid_times=valid_times,
                            dt_hours=self.config.market.dt,
                        ),
                    )
                )
        awarded_commitment = (
            AwardedCommitment.aggregate(tuple(awarded_commitments))
            if awarded_commitments
            else None
        )
        if awarded_commitments:
            assert awarded_commitment is not None
            final_input_versions = derive_input_versions(
                events,
                extra={"forecast_registry": str(self.forecast_registry)},
            )
            day_ahead = solve_day_ahead_operational(
                net_load_forecast,
                price_forecast,
                self.bess,
                self.config,
                q_long=q_long,
                p_long=p_long,
                p_ref=price_forecast,
                scenario_set=scenarios,
                decision_time=dispatch_time,
                input_versions=final_input_versions,
                config_version=self.config_version,
                settlement=self.market_mode.settlement,
                awarded_commitment=awarded_commitment,
                solver=self.solver,
            )
            trace = day_ahead.decision_trace
            if trace is not None:
                trace.diagnostics = {
                    **(
                        provisional_day_ahead.decision_trace.diagnostics
                        if provisional_day_ahead.decision_trace is not None
                        else {}
                    ),
                    **trace.diagnostics,
                    "award.commitment_ids": ",".join(awarded_commitment.award_ids),
                }
        else:
            day_ahead = provisional_day_ahead
        multi_resource_result = None
        resource_operational_plan = None
        resource_execution_deviations: tuple[ResourceExecutionDeviation, ...] = ()
        if self.multi_resource_portfolio is not None:
            multi_resource_result = solve_multi_resource(
                load_mwh=load_energy,
                price=price_forecast,
                bess_units=self.multi_resource_portfolio.bess_units,
                dr_units=self.multi_resource_portfolio.dr_units,
                renewable_units=self.multi_resource_portfolio.renewable_units,
                dt=self.config.market.dt,
                solver=self.solver,
            )
            if multi_resource_result.grid_import_mwh is not None:
                resource_operational_plan = (
                    ResourceOperationalPlan.from_multi_resource_result(
                        result=multi_resource_result,
                        valid_times=valid_times,
                        dt_hours=self.config.market.dt,
                        plan_version=f"multi-resource-plan:{self.config_version}",
                    )
                )
            trace = day_ahead.decision_trace
            if trace is not None:
                trace.diagnostics = {
                    **trace.diagnostics,
                    "multi_resource.solve_status": (
                        multi_resource_result.solve_result.status.value
                    ),
                }
            if resource_meterings:
                metering_by_resource = {
                    metering.resource_id: metering for metering in resource_meterings
                }
                if len(metering_by_resource) != len(resource_meterings):
                    raise ValueError("resource_meterings must not contain duplicate resource IDs")
                planned_bess_ids = {
                    unit.name for unit in self.multi_resource_portfolio.bess_units
                }
                unknown_ids = set(metering_by_resource) - planned_bess_ids
                if unknown_ids:
                    raise ValueError(
                        "resource_meterings contain resources outside the multi-resource "
                        f"BESS portfolio: {sorted(unknown_ids)!r}"
                    )
                missing_ids = planned_bess_ids - set(metering_by_resource)
                if missing_ids:
                    raise ValueError(
                        "resource_meterings must cover every multi-resource BESS: "
                        f"{sorted(missing_ids)!r}"
                    )
                resource_execution_deviations = tuple(
                    ResourceExecutionDeviation.from_planned_discharge(
                        resource_id=unit.name,
                        planned_interval_discharge_mwh=pd.Series(
                            np.asarray(
                                multi_resource_result.resource_schedules[unit.name][
                                    "p_discharge"
                                ],
                                dtype=float,
                            )
                            * self.config.market.dt,
                            index=valid_times,
                        ),
                        metering=metering_by_resource[unit.name],
                        plan_version=f"multi-resource-plan:{self.config_version}",
                    )
                    for unit in self.multi_resource_portfolio.bess_units
                )
        elif resource_meterings:
            raise ValueError(
                "resource_meterings require a configured multi_resource_portfolio"
            )
        events.append(
            DispatchEvent(
                issue_time=dispatch_time,
                valid_time=cast(pd.Timestamp, valid_times[0]),
                version=_dispatch_model_tag(day_ahead),
                source="dispatch:day_ahead",
                calendar=calendar,
                unit="MW",
            )
        )
        events.extend(late_award_events)

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
        intraday_admission = self._admit_scenarios(
            stage="intraday",
            bundle=intraday_forecasts,
            scenario_set=intraday_scenarios,
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
        constraint_tightening = (
            self.execution_bias_estimator.constraint_tightening()
            if self.execution_bias_estimator is not None
            else None
        )
        intraday_awarded_commitments = [
            commitment
            for receipt_time, commitment in late_awarded_commitments
            if receipt_time <= intraday_decision_time
        ]
        intraday_awarded_commitment = (
            AwardedCommitment.aggregate(tuple(intraday_awarded_commitments))
            if intraday_awarded_commitments
            else None
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
            constraint_tightening=constraint_tightening,
            awarded_commitment=intraday_awarded_commitment,
        )
        self._record_scenario_admission(
            intraday.schedule,
            intraday_admission,
        )
        multi_resource_intraday_plan = None
        if (
            multi_resource_actual_soc_mwh is not None
            or multi_resource_actuals is not None
        ):
            if self.multi_resource_portfolio is None or multi_resource_result is None:
                raise ValueError(
                    "multi-resource actuals require a multi_resource_portfolio"
                )
            remaining_dr_units: list[DemandResponseUnit] = []
            for unit in self.multi_resource_portfolio.dr_units:
                start, end = unit.window
                relative_start = max(0, start - intraday_start)
                relative_end = min(horizon - intraday_start, end - intraday_start)
                if relative_end > relative_start:
                    remaining_dr_units.append(
                        replace(unit, window=(relative_start, relative_end))
                    )
            remaining_renewables = tuple(
                replace(unit, available_mw=unit.available_mw[intraday_start:])
                for unit in self.multi_resource_portfolio.renewable_units
            )
            multi_resource_intraday_plan = solve_multi_resource_intraday(
                load_mwh=intraday_load_energy,
                price=intraday_price_forecast,
                bess_units=self.multi_resource_portfolio.bess_units,
                dr_units=tuple(remaining_dr_units),
                renewable_units=remaining_renewables,
                previous_result=multi_resource_result,
                executed_count=intraday_start,
                actual_soc_mwh=multi_resource_actual_soc_mwh,
                actuals=multi_resource_actuals,
                dt=self.config.market.dt,
                solver=self.solver,
            )
            if multi_resource_actuals is not None and resource_operational_plan is not None:
                resource_operational_plan = resource_operational_plan.with_actuals(
                    multi_resource_actuals
                )
            trace = day_ahead.decision_trace
            if trace is not None:
                trace.diagnostics = {
                    **trace.diagnostics,
                    "multi_resource.intraday_fallback": str(
                        multi_resource_intraday_plan.fallback_used
                    ).lower(),
                }
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

        # ---- V5-9：提供正式账单时自动对账；模式不支持则显式失败 ----
        reconciliation_report = None
        if billing_statement is not None:
            reconcile = getattr(self.market_mode, "reconcile_statement", None)
            if not callable(reconcile):
                raise ValueError(
                    "market mode does not support statement reconciliation"
                )
            reconciliation_report = reconcile(
                report=settlement,
                billing_statement=billing_statement,
            )

        fulfillment_commitments = [*awarded_commitments, *intraday_awarded_commitments]
        award_fulfillments = (
            tuple(
                AwardFulfillment.from_commitment(
                    commitment=commitment,
                    metering=resource_metering,
                )
                for commitment in fulfillment_commitments
            )
            if resource_metering is not None
            else ()
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
            reconciliation_report=reconciliation_report,
            intraday_forecasts=intraday_forecasts,
            intraday_scenarios=intraday_scenarios,
            multi_resource_result=multi_resource_result,
            resource_operational_plan=resource_operational_plan,
            multi_resource_intraday_plan=multi_resource_intraday_plan,
            award_fulfillments=award_fulfillments,
            resource_execution_deviations=resource_execution_deviations,
            scenario_admissions=(day_ahead_admission, intraday_admission),
        )
