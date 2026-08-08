"""V5 已成交承诺的调度时段映射。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain.contracts import (
    AwardedCommitment,
    BidSubmission,
    MarketAwardReceipt,
    match_award_receipt,
)

ISSUE_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
DELIVERY_START = ISSUE_TIME + pd.Timedelta(minutes=15)
DELIVERY_END = DELIVERY_START + pd.Timedelta(minutes=30)
VALID_TIMES = pd.date_range(DELIVERY_START, periods=4, freq="15min")


def _matched_award():
    bid = BidSubmission(
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
    receipt = MarketAwardReceipt(
        award_id="award-001",
        receipt_time=ISSUE_TIME,
        delivery_start=DELIVERY_START,
        delivery_end=DELIVERY_END,
        cleared_quantity_mwh=0.5,
        cleared_price_cny_per_mwh=320.0,
        source_version="receipt-v1",
        bid_id=bid.bid_id,
    )
    return match_award_receipt(receipt=receipt, bid=bid)


def test_awarded_commitment_maps_energy_to_aligned_delivery_periods() -> None:
    """成交能量只分配到回执覆盖的调度时段，且保持能量守恒。"""
    commitment = AwardedCommitment.from_matched_award(
        _matched_award(),
        valid_times=VALID_TIMES,
        dt_hours=0.25,
    )

    assert commitment.direction == "sell"
    assert commitment.required_energy_mwh.index.equals(VALID_TIMES)
    assert commitment.required_energy_mwh.iloc[:2].sum() == pytest.approx(0.5)
    assert commitment.required_energy_mwh.iloc[2:].sum() == pytest.approx(0.0)
