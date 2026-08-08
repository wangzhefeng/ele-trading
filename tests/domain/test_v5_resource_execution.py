"""V5 资源级计划—实测执行偏差契约。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain.contracts import (
    ResourceExecutionDeviation,
    ResourceMetering,
)


INDEX = pd.date_range(
    "2026-07-01 00:15",
    periods=2,
    freq="15min",
    tz="Asia/Shanghai",
)


def _metering(values: list[float]) -> ResourceMetering:
    return ResourceMetering(
        resource_id="bess-001",
        observed_at=pd.Timestamp("2026-07-01 01:00", tz="Asia/Shanghai"),
        interval_discharge_mwh=pd.Series(values, index=INDEX),
        source_version="meter-v1",
    )


def test_resource_execution_deviation_uses_aligned_external_metering():
    deviation = ResourceExecutionDeviation.from_planned_discharge(
        resource_id="bess-001",
        planned_interval_discharge_mwh=pd.Series([0.25, 0.25], index=INDEX),
        metering=_metering([0.20, 0.30]),
        plan_version="dispatch-v1",
    )

    assert deviation.planned_discharge_mwh == pytest.approx(0.5)
    assert deviation.actual_discharge_mwh == pytest.approx(0.5)
    assert deviation.shortfall_mwh == pytest.approx(0.0)
    assert deviation.metering_version == "meter-v1"
    assert deviation.plan_version == "dispatch-v1"


def test_resource_execution_deviation_rejects_resource_or_interval_mismatch():
    with pytest.raises(ValueError, match="resource_id"):
        ResourceExecutionDeviation.from_planned_discharge(
            resource_id="bess-other",
            planned_interval_discharge_mwh=pd.Series([0.25, 0.25], index=INDEX),
            metering=_metering([0.25, 0.25]),
            plan_version="dispatch-v1",
        )
    with pytest.raises(ValueError, match="cover"):
        ResourceExecutionDeviation.from_planned_discharge(
            resource_id="bess-001",
            planned_interval_discharge_mwh=pd.Series([0.25, 0.25], index=INDEX),
            metering=ResourceMetering(
                resource_id="bess-001",
                observed_at=pd.Timestamp("2026-07-01 01:00", tz="Asia/Shanghai"),
                interval_discharge_mwh=pd.Series([0.5], index=INDEX[:1]),
                source_version="meter-v1",
            ),
            plan_version="dispatch-v1",
        )
