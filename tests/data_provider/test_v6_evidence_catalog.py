"""V6-0 证据目录：外部资料未授权或不可用时不得晋级。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.data_provider.contracts import (
    DataAvailabilityRecord,
    DataCatalog,
    EvidenceCatalogEntry,
)


AS_OF = pd.Timestamp("2026-08-09 08:00", tz="Asia/Shanghai")


def _entry(*, permission: str = "authorized") -> EvidenceCatalogEntry:
    return EvidenceCatalogEntry(
        catalog_id="metering-day-1",
        artifact_type="resource_metering",
        owner="operations-team",
        permission=permission,
        retention_policy="seven-years",
        availability=DataAvailabilityRecord(
            source_id="metering-provider",
            event_time=AS_OF - pd.Timedelta(hours=1),
            published_at=AS_OF - pd.Timedelta(minutes=30),
            available_at=AS_OF,
            version="meter-v1",
            revision=1,
        ),
    )


def test_data_catalog_requires_authorized_available_evidence():
    catalog = DataCatalog(entries={"metering-day-1": _entry()})

    assert catalog.require_available("metering-day-1", as_of=AS_OF).availability.version == "meter-v1"
    with pytest.raises(ValueError, match="permission"):
        DataCatalog(entries={"metering-day-1": _entry(permission="pending")}).require_available(
            "metering-day-1", as_of=AS_OF
        )
