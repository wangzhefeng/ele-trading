"""V5 Award 回执与已提交报价的匹配约束。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain.contracts import (
    BidSubmission,
    MarketAwardReceipt,
    match_award_receipt,
)

ISSUE_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
DELIVERY_START = ISSUE_TIME + pd.Timedelta(minutes=15)
DELIVERY_END = DELIVERY_START + pd.Timedelta(minutes=30)


def _bid() -> BidSubmission:
    return BidSubmission(
        bid_id="bid-001",
        market="test-market",
        product="energy",
        direction="sell",
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


def _receipt(**overrides: object) -> MarketAwardReceipt:
    values: dict[str, object] = {
        "award_id": "award-001",
        "receipt_time": ISSUE_TIME,
        "delivery_start": DELIVERY_START,
        "delivery_end": DELIVERY_END,
        "cleared_quantity_mwh": 0.5,
        "cleared_price_cny_per_mwh": 320.0,
        "source_version": "receipt-v1",
        "bid_id": "bid-001",
    }
    values.update(overrides)
    return MarketAwardReceipt(**values)  # type: ignore[arg-type]


def test_match_award_receipt_rejects_delivery_window_outside_bid() -> None:
    """已知 bid 的回执交割窗口不得超出该报价窗口。"""
    receipt = _receipt(delivery_end=DELIVERY_END + pd.Timedelta(minutes=15))

    with pytest.raises(ValueError, match="delivery"):
        match_award_receipt(receipt=receipt, bid=_bid())


def test_match_award_receipt_rejects_cumulative_quantity_above_bid() -> None:
    """部分成交累计后不得超过本周期提交的报价量。"""
    receipt = _receipt(cleared_quantity_mwh=0.7)

    with pytest.raises(ValueError, match="quantity"):
        match_award_receipt(
            receipt=receipt,
            bid=_bid(),
            already_awarded_mwh=0.4,
        )
