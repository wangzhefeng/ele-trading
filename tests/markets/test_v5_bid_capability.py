"""V5-8 市场模式报价 capability 测试。"""

from __future__ import annotations

from typing import cast

import pandas as pd

from ele_trading.domain import BidSubmission
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE

ISSUE_TIME = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-01 08:00", tz="Asia/Shanghai"),
)
DELIVERY_START = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-02 00:00", tz="Asia/Shanghai"),
)
DELIVERY_END = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-02 00:15", tz="Asia/Shanghai"),
)


def _bid() -> BidSubmission:
    return BidSubmission(
        bid_id="bid-20260701-001",
        market="single_settlement",
        product="energy",
        direction="buy",
        issue_time=ISSUE_TIME,
        delivery_start=DELIVERY_START,
        delivery_end=DELIVERY_END,
        quantity_mwh=1.0,
        price_cny_per_mwh=300.0,
        forecast_version="forecast-v1",
        rule_version="rule-v1",
        resource_version="resource-v1",
        strategy_version="strategy-v1",
        config_version="config-v1",
    )


def test_single_settlement_explicitly_reports_plan_only_submission_mode():
    """单结算当前只支持运行计划，必须明确拒绝正式报价。"""
    capability = SINGLE_SETTLEMENT_MODE.bid_submission_capability
    decision = capability.validate_submission(_bid())

    assert capability.can_submit is False
    assert decision.accepted is False
    assert decision.reason
    assert "plan-only" in decision.reason
