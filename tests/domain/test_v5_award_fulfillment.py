"""V5 Award 承诺只能由版本化资源实测结算履约差额。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain.contracts import (
    AwardFulfillment,
    AwardedCommitment,
    ResourceMetering,
)


INDEX = pd.date_range(
    "2026-07-01 00:15",
    periods=2,
    freq="15min",
    tz="Asia/Shanghai",
)


def _commitment() -> AwardedCommitment:
    return AwardedCommitment(
        award_id="award-001",
        bid_id="bid-001",
        external_award_reference=None,
        market="test-market",
        product="energy",
        direction="sell",
        required_energy_mwh=pd.Series([0.25, 0.25], index=INDEX),
        source_version="clearing-v1",
    )


def test_versioned_resource_metering_calculates_award_shortfall():
    metering = ResourceMetering(
        resource_id="bess-001",
        observed_at=pd.Timestamp("2026-07-01 01:00", tz="Asia/Shanghai"),
        interval_discharge_mwh=pd.Series([0.25, 0.10], index=INDEX),
        source_version="meter-v1",
    )

    fulfillment = AwardFulfillment.from_commitment(
        commitment=_commitment(),
        metering=metering,
    )

    assert fulfillment.award_ids == ("award-001",)
    assert fulfillment.committed_mwh == pytest.approx(0.5)
    assert fulfillment.delivered_mwh == pytest.approx(0.35)
    assert fulfillment.shortfall_mwh == pytest.approx(0.15)
    assert fulfillment.metering_version == "meter-v1"


def test_metering_requires_exact_award_interval_coverage():
    metering = ResourceMetering(
        resource_id="bess-001",
        observed_at=pd.Timestamp("2026-07-01 01:00", tz="Asia/Shanghai"),
        interval_discharge_mwh=pd.Series([0.5], index=INDEX[:1]),
        source_version="meter-v1",
    )

    with pytest.raises(ValueError, match="cover"):
        AwardFulfillment.from_commitment(
            commitment=_commitment(),
            metering=metering,
        )
