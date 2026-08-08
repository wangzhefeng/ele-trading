"""V5-8 Bid/Award 事件关联契约测试。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from ele_trading.domain import AwardEvent, BidEvent, MarketCalendar, PositionEvent


CALENDAR = MarketCalendar(
    market="target-market",
    tz="Asia/Shanghai",
    freq_minutes=15,
    settle_periods=96,
)


def _event_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "issue_time": pd.Timestamp("2026-07-01 08:00", tz="Asia/Shanghai"),
        "valid_time": pd.Timestamp("2026-07-02 00:00", tz="Asia/Shanghai"),
        "version": "market-v1",
        "source": "market:bid",
        "calendar": CALENDAR,
        "unit": "MWh",
    }
    values.update(overrides)
    return values


def test_bid_event_requires_non_empty_bid_id():
    """已提交报价事件必须关联一个已定义报价。"""
    with pytest.raises(ValueError, match="bid_id"):
        BidEvent(**_event_kwargs(bid_id=" "))


@pytest.mark.parametrize(
    ("bid_id", "external_award_reference"),
    ((None, None), ("bid-1", "contract-1")),
)
def test_award_event_requires_exactly_one_award_source(
    bid_id: str | None,
    external_award_reference: str | None,
):
    """市场成交或外部成交导入必须具有且仅具有一个来源。"""
    with pytest.raises(ValueError, match="exactly one"):
        AwardEvent(
            **_event_kwargs(
                source="market:award",
                award_id="award-1",
                bid_id=bid_id,
                external_award_reference=external_award_reference,
            )
        )


def test_position_event_is_independent_from_market_award():
    """外部合同头寸进入决策 trace 时不伪装为本周期市场成交。"""
    event = PositionEvent(**_event_kwargs(source="position"))

    assert event.source == "position"
