"""V6-0 MarketProfile：未确认规则只能停留在 plan-only，不能猜测正式市场语义。"""

from __future__ import annotations

import pytest
import pandas as pd

from ele_trading.data_provider.contracts import (
    DataAvailabilityRecord,
    DataCatalog,
    EvidenceCatalogEntry,
)
from ele_trading.markets.dual_settlement.mode import DUAL_SETTLEMENT_MODE
from ele_trading.markets.profile import (
    DataAdmissionPolicy,
    MarketInputEvidence,
    MarketPhase,
    MarketProfile,
    MarketProfileRejected,
)
from ele_trading.markets.protocol import MarketMode
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE


@pytest.mark.parametrize(
    "mode",
    [SINGLE_SETTLEMENT_MODE, DUAL_SETTLEMENT_MODE],
    ids=["single_settlement", "dual_settlement"],
)
def test_existing_modes_expose_explicit_plan_only_market_profile(mode):
    assert isinstance(mode, MarketMode)
    profile = mode.market_profile

    assert profile.market_id == mode.name
    assert profile.plan_only
    assert profile.rule_version == "unconfirmed"
    assert not profile.is_formal_path


def test_plan_only_profile_rejects_any_formal_market_phase():
    profile = SINGLE_SETTLEMENT_MODE.market_profile

    with pytest.raises(MarketProfileRejected, match="plan-only"):
        profile.require_formal_phase(MarketPhase.BID)


def test_plan_only_profile_rejects_unmapped_commitment_projection():
    profile = SINGLE_SETTLEMENT_MODE.market_profile

    with pytest.raises(MarketProfileRejected, match="unmapped product/direction"):
        profile.project_commitment(product="energy", direction="sell")


def test_markets_package_exports_market_profile_contract():
    import ele_trading.markets as markets

    assert markets.MarketProfile is MarketProfile


def test_data_admission_requires_timeline_quality_completeness_revision_and_permission():
    policy = DataAdmissionPolicy(
        required_rule_version="rule-v1",
        admitted_sources=frozenset({"archived-provider"}),
    )
    evidence = MarketInputEvidence(
        source="archived-provider",
        available_at=pd.Timestamp("2026-08-09 08:00", tz="Asia/Shanghai"),
        quality="approved",
        complete=True,
        revision="revision-1",
        permission="authorized",
        rule_version="rule-v1",
    )

    policy.admit(
        evidence=evidence,
        decision_time=pd.Timestamp("2026-08-09 08:00", tz="Asia/Shanghai"),
    )
    with pytest.raises(MarketProfileRejected, match="available after"):
        policy.admit(
            evidence=evidence,
            decision_time=pd.Timestamp("2026-08-09 07:59", tz="Asia/Shanghai"),
        )


def test_data_admission_requires_catalog_link_when_configured():
    available_at = pd.Timestamp("2026-08-09 08:00", tz="Asia/Shanghai")
    catalog = DataCatalog(
        entries={
            "price-v1": EvidenceCatalogEntry(
                catalog_id="price-v1",
                artifact_type="price_history",
                owner="market-data",
                permission="authorized",
                retention_policy="seven-years",
                availability=DataAvailabilityRecord(
                    source_id="archived-provider",
                    event_time=available_at,
                    published_at=available_at,
                    available_at=available_at,
                    version="revision-1",
                ),
            ),
        }
    )
    policy = DataAdmissionPolicy(
        required_rule_version="rule-v1",
        admitted_sources=frozenset({"archived-provider"}),
        catalog=catalog,
    )
    evidence = MarketInputEvidence(
        source="archived-provider",
        available_at=available_at,
        quality="approved",
        complete=True,
        revision="revision-1",
        permission="authorized",
        rule_version="rule-v1",
        catalog_id="price-v1",
    )

    policy.admit(evidence=evidence, decision_time=available_at)
