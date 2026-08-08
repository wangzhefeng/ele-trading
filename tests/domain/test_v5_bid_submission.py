"""V5-8 市场无关报价契约测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain import BidSubmission

ISSUE_TIME = pd.Timestamp("2026-07-01 08:00", tz="Asia/Shanghai")
DELIVERY_START = pd.Timestamp("2026-07-02 00:00", tz="Asia/Shanghai")
DELIVERY_END = pd.Timestamp("2026-07-02 00:15", tz="Asia/Shanghai")


def _bid(**overrides) -> BidSubmission:
    values = {
        "bid_id": "bid-20260701-001",
        "market": "target-market",
        "product": "energy",
        "direction": "buy",
        "issue_time": ISSUE_TIME,
        "delivery_start": DELIVERY_START,
        "delivery_end": DELIVERY_END,
        "quantity_mwh": 1.0,
        "price_cny_per_mwh": 300.0,
        "forecast_version": "forecast-v1",
        "rule_version": "rule-v1",
        "resource_version": "resource-v1",
        "strategy_version": "strategy-v1",
        "config_version": "config-v1",
    }
    values.update(overrides)
    return BidSubmission(**values)


def test_bid_submission_requires_traceable_delivery_and_versions():
    """报价必须携带交割区间和所有决策证据版本。"""
    bid = _bid()

    assert bid.delivery_start < bid.delivery_end


@pytest.mark.parametrize(
    "field_name",
    (
        "bid_id",
        "market",
        "product",
        "direction",
        "forecast_version",
        "rule_version",
        "resource_version",
        "strategy_version",
        "config_version",
    ),
)
def test_bid_submission_rejects_empty_traceability_fields(field_name: str):
    """识别、市场与决策证据版本均不得为空。"""
    with pytest.raises(ValueError, match=field_name):
        _bid(**{field_name: " "})


@pytest.mark.parametrize(
    "field_name",
    ("issue_time", "delivery_start", "delivery_end"),
)
def test_bid_submission_rejects_naive_timestamps(field_name: str):
    """无法判定可见性与交割边界的 naive 时间戳不得进入报价。"""
    with pytest.raises(ValueError, match=field_name):
        _bid(**{field_name: pd.Timestamp("2026-07-01 08:00")})


def test_bid_submission_rejects_invalid_delivery_interval():
    """报价交割区间必须为正，且不得在签发时刻前开始。"""
    with pytest.raises(ValueError, match="delivery_end"):
        _bid(delivery_end=DELIVERY_START)

    with pytest.raises(ValueError, match="delivery_start"):
        _bid(delivery_start=ISSUE_TIME - pd.Timedelta(minutes=15))


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("quantity_mwh", 0.0),
        ("quantity_mwh", -1.0),
        ("quantity_mwh", float("nan")),
        ("quantity_mwh", float("inf")),
        ("price_cny_per_mwh", float("nan")),
        ("price_cny_per_mwh", float("inf")),
    ),
)
def test_bid_submission_rejects_invalid_quantity_or_price(
    field_name: str,
    value: float,
):
    """申报量必须为正有限数；电价可为负，但不能为非有限数。"""
    with pytest.raises(ValueError, match=field_name):
        _bid(**{field_name: value})


def test_bid_submission_allows_negative_energy_price():
    """电价不是物理量，负价不得被通用非负校验误拒。"""
    assert _bid(price_cny_per_mwh=-20.0).price_cny_per_mwh == -20.0
