"""V5-8 Bid→Award→Dispatch 主链接线测试。

不回填真实市场规则：接受的 capability 是测试内的真实 Protocol 实现；
Award 只能由显式 MarketAwardReceipt 输入构造，编排器不得伪造成交。
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest

from ele_trading.domain.contracts import (
    BidSubmission,
    MarketAwardReceipt,
    PositionState,
)
from ele_trading.domain.events import (
    AwardEvent,
    BidEvent,
    DispatchEvent,
    ForecastEvent,
    MeteringEvent,
    PositionEvent,
    SettlementEvent,
)
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.markets.protocol import BidSubmissionDecision
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.orchestrator import TradingOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
DECISION_TIME = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai"),
)
BESS = {
    "p_bcmax": 2.0,
    "p_bdmax": 2.0,
    "p_bceff": 0.95,
    "p_bdeff": 0.95,
    "socmin": 1.0,
    "socmax": 5.0,
    "socini": 3.0,
    "cap": 4.0,
}


class _DataProvider:
    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="position-v1",
        )


class _ForecastProvider:
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        values = {
            "price": [200.0, 250.0, 500.0, 600.0],
            "load": [3.0, 3.0, 3.0, 3.0],
            "wind_power": [0.0, 0.0, 0.0, 0.0],
            "pv_power": [0.0, 0.0, 0.0, 0.0],
        }[request.target][: request.horizon]
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        point = pd.Series(values, index=index)
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - 0.1, 0.9: point + 0.1},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}-v1",
            feature_as_of=request.issue_time,
        )


def _build_candidate(
    *,
    decision_time,
    valid_times,
    plan,
    bundle,
    config_version,
) -> BidSubmission:
    """从日前计划构造候选报价（测试内的真实构造逻辑，非 mock）。"""
    discharge_mwh = float(plan.resource_schedule["p_discharge"].sum()) * 0.25
    return BidSubmission(
        bid_id="bid-20260701-001",
        market="mengxi",
        product="day_ahead_energy",
        direction="sell",
        issue_time=decision_time,
        delivery_start=valid_times[0],
        delivery_end=valid_times[-1] + pd.Timedelta(minutes=15),
        quantity_mwh=max(discharge_mwh, 0.5),
        price_cny_per_mwh=float(bundle.price_forecast.point.mean()),
        forecast_version=bundle.price_forecast.model_version,
        rule_version=config_version,
        resource_version="bess-v1",
        strategy_version="strategy-v1",
        config_version=config_version,
    )


class _AcceptingCapability:
    """测试内的真实 BidSubmissionCapability 实现：结构合法即接受。"""

    can_submit: bool = True

    def validate_submission(self, bid: BidSubmission) -> BidSubmissionDecision:
        return BidSubmissionDecision(accepted=True)


class _AcceptingMode:
    """包装单结算模式，仅替换报价 capability。"""

    name = "test_accepting"
    settlement = SINGLE_SETTLEMENT_MODE.settlement
    bid_submission_capability = _AcceptingCapability()
    price_roles = SINGLE_SETTLEMENT_MODE.price_roles
    day_ahead_price_role = SINGLE_SETTLEMENT_MODE.day_ahead_price_role
    intraday_price_role = SINGLE_SETTLEMENT_MODE.intraday_price_role

    def load_config(self, path):
        return SINGLE_SETTLEMENT_MODE.load_config(path)


def _run(
    *,
    mode=None,
    bid_candidate_builder=None,
    award_receipts=(),
):
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=_DataProvider(),
        forecast_provider=_ForecastProvider(),
        forecast_registry="registry-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=mode or SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v1",
        bid_candidate_builder=bid_candidate_builder,
    )
    return orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
        award_receipts=award_receipts,
    )


def _receipt(**overrides) -> MarketAwardReceipt:
    values = {
        "award_id": "award-001",
        "bid_id": "bid-20260701-001",
        "external_award_reference": None,
        "receipt_time": DECISION_TIME + pd.Timedelta(minutes=5),
        "delivery_start": DECISION_TIME + pd.Timedelta(minutes=15),
        "delivery_end": DECISION_TIME + pd.Timedelta(hours=1),
        "cleared_quantity_mwh": 0.5,
        "cleared_price_cny_per_mwh": 412.5,
        "source_version": "clearing-v1",
    }
    values.update(overrides)
    return MarketAwardReceipt(**values)


def test_plan_only_mode_rejects_candidate_with_recorded_reason():
    """单结算 plan-only：候选报价被结构化拒绝，不产生 Bid/Award。"""
    result = _run(bid_candidate_builder=_build_candidate)

    assert not any(isinstance(event, BidEvent) for event in result.events)
    assert not any(isinstance(event, AwardEvent) for event in result.events)
    trace = result.day_ahead_plan.decision_trace
    assert trace is not None
    assert trace.diagnostics["bid.submitted"] == "false"
    assert "operational planning only" in trace.diagnostics["bid.rejected_reason"]


def test_accepted_bid_and_matching_receipt_form_ordered_chain():
    """接受的 Bid + 匹配回执 → Bid→Award 有序出现在事件链中。"""
    result = _run(
        mode=_AcceptingMode(),
        bid_candidate_builder=_build_candidate,
        award_receipts=(_receipt(),),
    )

    event_types = [type(event) for event in result.events]
    assert event_types == (
        [PositionEvent]
        + [ForecastEvent] * 5
        + [DispatchEvent, BidEvent, AwardEvent]
        + [ForecastEvent] * 4
        + [DispatchEvent, MeteringEvent, SettlementEvent]
    )
    bid_event = next(e for e in result.events if isinstance(e, BidEvent))
    award_event = next(e for e in result.events if isinstance(e, AwardEvent))
    assert bid_event.bid_id == "bid-20260701-001"
    assert award_event.bid_id == bid_event.bid_id
    assert award_event.award_id == "award-001"
    trace = result.day_ahead_plan.decision_trace
    assert trace is not None
    assert trace.diagnostics["bid.submitted"] == "true"


def test_accepted_bid_without_receipt_does_not_fabricate_award():
    """有 Bid 无回执：只允许 BidEvent，不得自动提升为 Award。"""
    result = _run(
        mode=_AcceptingMode(),
        bid_candidate_builder=_build_candidate,
    )

    assert any(isinstance(event, BidEvent) for event in result.events)
    assert not any(isinstance(event, AwardEvent) for event in result.events)


def test_receipt_referencing_unknown_bid_is_rejected():
    """回执引用本次未提交的 bid_id：显式失败，不静默接受。"""
    with pytest.raises(ValueError, match="unknown bid"):
        _run(
            mode=_AcceptingMode(),
            bid_candidate_builder=_build_candidate,
            award_receipts=(_receipt(bid_id="bid-not-submitted"),),
        )


def test_external_receipt_uses_external_reference_without_bid():
    """外部已成交：显式 external_award_reference，不伪装成本周期 Bid。"""
    result = _run(
        award_receipts=(
            _receipt(
                bid_id=None,
                external_award_reference="mlt-contract-2026-07#01",
            ),
        ),
    )

    award_event = next(e for e in result.events if isinstance(e, AwardEvent))
    assert award_event.external_award_reference == "mlt-contract-2026-07#01"
    assert award_event.bid_id is None
    assert not any(isinstance(event, BidEvent) for event in result.events)
