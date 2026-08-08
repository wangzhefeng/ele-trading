"""Domain event contracts: market calendar, delivery period and units (v3 M0)."""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain import (
    AwardEvent,
    BidEvent,
    DispatchEvent,
    ForecastEvent,
    MarketCalendar,
    MeteringEvent,
    PositionEvent,
    SettlementEvent,
    TradingEvent,
)


def _calendar(**overrides) -> MarketCalendar:
    values = {
        "market": "mengxi",
        "tz": "Asia/Shanghai",
        "freq_minutes": 15,
        "settle_periods": 96,
    }
    values.update(overrides)
    return MarketCalendar(**values)


def _event(event_type=TradingEvent, /, **overrides):
    values = {
        "issue_time": pd.Timestamp("2026-07-01 08:00", tz="Asia/Shanghai"),
        "valid_time": pd.Timestamp("2026-07-02 00:00", tz="Asia/Shanghai"),
        "version": "2026.08.02",
        "source": "forecast",
        "calendar": _calendar(),
        "unit": "MW",
    }
    values.update(overrides)
    return event_type(**values)


# ---------------- MarketCalendar 校验 ----------------


@pytest.mark.parametrize("field", ["market", "tz"])
def test_calendar_rejects_empty_identity_fields(field: str):
    """Empty market or timezone must not define a market calendar."""
    with pytest.raises(ValueError, match=field):
        _calendar(**{field: " "})


def test_calendar_rejects_unknown_timezone():
    with pytest.raises(ValueError, match="tz"):
        _calendar(tz="Not/A_Timezone")


@pytest.mark.parametrize(
    ("freq_minutes", "settle_periods"),
    [
        (0, 96),      # 非正粒度
        (15, 0),      # 非正时段数
        (15.0, 96),   # 非整型粒度
        (30, 96),     # 30*96=2880 ≠ 1440，不能覆盖完整交易日
    ],
)
def test_calendar_rejects_invalid_granularity(freq_minutes, settle_periods):
    """交割粒度与时段数的乘积必须覆盖完整交易日（1440 分钟）。"""
    with pytest.raises(ValueError):
        _calendar(freq_minutes=freq_minutes, settle_periods=settle_periods)


# ---------------- TradingEvent 校验 ----------------


@pytest.mark.parametrize("field", ["issue_time", "valid_time"])
def test_event_rejects_naive_timestamps(field: str):
    """Naive timestamps must not enter the decision-trace event chain."""
    with pytest.raises(ValueError, match=field):
        _event(**{field: pd.Timestamp("2026-07-01 08:00")})


@pytest.mark.parametrize("field", ["version", "source", "unit"])
def test_event_rejects_empty_traceability_fields(field: str):
    """Version, source and unit are mandatory traceability evidence."""
    with pytest.raises(ValueError, match=field):
        _event(**{field: ""})


def test_event_rejects_non_calendar():
    with pytest.raises(ValueError, match="calendar"):
        _event(calendar="mengxi")


def test_event_delivery_period_uses_calendar_granularity():
    """交割时段 = [valid_time, valid_time + freq_minutes)。"""
    event = _event()
    start, end = event.delivery_period

    assert start == pd.Timestamp("2026-07-02 00:00", tz="Asia/Shanghai")
    assert end == start + pd.Timedelta(minutes=15)


# ---------------- 事件链子类可构造 ----------------


@pytest.mark.parametrize(
    ("event_type", "overrides"),
    (
        (ForecastEvent, {}),
        (PositionEvent, {}),
        (BidEvent, {"bid_id": "bid-1"}),
        (AwardEvent, {"award_id": "award-1", "bid_id": "bid-1"}),
        (DispatchEvent, {}),
        (MeteringEvent, {}),
        (SettlementEvent, {}),
    ),
)
def test_event_chain_subclasses_are_constructible(event_type, overrides):
    """Position→Forecast→Bid→Award→Dispatch→Metering→Settlement 全链可构造。"""
    event = _event(event_type, **overrides)

    assert isinstance(event, TradingEvent)
    assert event.calendar.settle_periods == 96


# ------------------------------------------------------------------ #
#  derive_input_versions（v3 M5）
# ------------------------------------------------------------------ #

def test_derive_input_versions_from_forecast_and_position():
    """input_versions = {source: version}，只取 Forecast/Position/Award 事件。"""
    from ele_trading.domain import derive_input_versions

    events = (
        _event(PositionEvent, source="position", version="position-v1"),
        _event(ForecastEvent, source="price", version="price-v2"),
        _event(ForecastEvent, source="load", version="load-v3"),
        _event(DispatchEvent, source="dispatch:day_ahead", version="model-v1"),
        _event(SettlementEvent, source="settlement:x", version="config-v1"),
    )
    versions = derive_input_versions(events)
    assert versions == {
        "position": "position-v1",
        "price": "price-v2",
        "load": "load-v3",
    }


def test_derive_input_versions_extra_merges_non_event_items():
    """extra 用于事件之外的版本项（如 forecast_registry）。"""
    from ele_trading.domain import derive_input_versions

    versions = derive_input_versions(
        [_event(ForecastEvent, source="price", version="price-v1")],
        extra={"forecast_registry": "registry-v1"},
    )
    assert versions == {
        "price": "price-v1",
        "forecast_registry": "registry-v1",
    }
