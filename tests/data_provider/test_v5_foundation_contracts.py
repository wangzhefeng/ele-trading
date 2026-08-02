"""v5 V5-0：数据可用性与规则快照契约。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.data_provider.contracts import (
    DataAvailabilityRecord,
    MarketDataSnapshot,
    RuleSnapshot,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-08-01 00:00",
                periods=2,
                freq="15min",
                tz="Asia/Shanghai",
            ),
            "is_observation": [True, True],
            "price": [100.0, 120.0],
        }
    )


def test_market_snapshot_accepts_only_records_available_by_as_of():
    record = DataAvailabilityRecord(
        source_id="market-price",
        event_time=pd.Timestamp("2026-08-01 00:00", tz="Asia/Shanghai"),
        published_at=pd.Timestamp("2026-08-01 00:20", tz="Asia/Shanghai"),
        available_at=pd.Timestamp("2026-08-01 00:22", tz="Asia/Shanghai"),
        version="price-v1",
    )

    snapshot = MarketDataSnapshot(
        market="single_settlement",
        scope_type="zone",
        scope_id="west",
        as_of=pd.Timestamp("2026-08-01 00:30", tz="Asia/Shanghai"),
        frame=_frame(),
        version="snapshot-v1",
        availability=(record,),
    )

    assert snapshot.availability == (record,)
    assert record.is_available_at(snapshot.as_of)


def test_market_snapshot_rejects_data_arriving_after_as_of():
    late_record = DataAvailabilityRecord(
        source_id="market-price",
        event_time=pd.Timestamp("2026-08-01 00:00", tz="Asia/Shanghai"),
        published_at=pd.Timestamp("2026-08-01 00:20", tz="Asia/Shanghai"),
        available_at=pd.Timestamp("2026-08-01 00:31", tz="Asia/Shanghai"),
        version="price-v2",
    )

    with pytest.raises(ValueError, match="availability records cannot be newer than as_of"):
        MarketDataSnapshot(
            market="single_settlement",
            scope_type="zone",
            scope_id="west",
            as_of=pd.Timestamp("2026-08-01 00:30", tz="Asia/Shanghai"),
            frame=_frame(),
            version="snapshot-v1",
            availability=(late_record,),
        )


def test_availability_record_rejects_arrival_before_publication():
    with pytest.raises(ValueError, match="available_at cannot be earlier than published_at"):
        DataAvailabilityRecord(
            source_id="market-price",
            event_time=pd.Timestamp("2026-08-01 00:00", tz="UTC"),
            published_at=pd.Timestamp("2026-08-01 00:20", tz="UTC"),
            available_at=pd.Timestamp("2026-08-01 00:19", tz="UTC"),
            version="price-v1",
        )


def test_rule_snapshot_distinguishes_known_time_from_effective_time():
    rule = RuleSnapshot(
        market="single_settlement",
        rule_version="2026-08",
        published_at=pd.Timestamp("2026-07-20 09:00", tz="Asia/Shanghai"),
        effective_from=pd.Timestamp("2026-08-01 00:00", tz="Asia/Shanghai"),
        effective_to=None,
        parameters={"price_cap": 1500.0},
        source_document="official-rule-2026-08.pdf",
        confirmed=True,
    )

    assert rule.is_known_at(pd.Timestamp("2026-07-21", tz="Asia/Shanghai"))
    assert not rule.is_effective_at(pd.Timestamp("2026-07-31", tz="Asia/Shanghai"))
    assert rule.is_effective_at(pd.Timestamp("2026-08-01", tz="Asia/Shanghai"))


def test_rule_snapshot_rejects_invalid_effective_window():
    with pytest.raises(ValueError, match="effective_to must be later than effective_from"):
        RuleSnapshot(
            market="single_settlement",
            rule_version="bad-window",
            published_at=pd.Timestamp("2026-07-20", tz="UTC"),
            effective_from=pd.Timestamp("2026-08-01", tz="UTC"),
            effective_to=pd.Timestamp("2026-07-31", tz="UTC"),
            parameters={},
            source_document="official.pdf",
            confirmed=False,
        )
