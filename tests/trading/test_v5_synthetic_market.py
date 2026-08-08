"""V5 synthetic 市场回放账本的工程边界。"""

from __future__ import annotations

import pytest

from ele_trading.trading.v5_simulation_fixtures import write_v5_simulation_fixtures
from ele_trading.trading.v5_synthetic_market import (
    SyntheticBidLedger,
    SyntheticBidStatus,
    load_synthetic_billing_statement,
    replay_synthetic_market_assets,
)


def test_synthetic_bid_ledger_replays_idempotent_award_without_formal_submission():
    ledger = SyntheticBidLedger()
    ledger.submit(bid_id="synthetic-bid-001")
    ledger.accept(bid_id="synthetic-bid-001")

    first = ledger.record_award(
        bid_id="synthetic-bid-001",
        award_id="synthetic-award-001",
    )
    replay = ledger.record_award(
        bid_id="synthetic-bid-001",
        award_id="synthetic-award-001",
    )

    assert first is replay
    assert first.simulation_only is True
    assert ledger.status_of("synthetic-bid-001") is SyntheticBidStatus.AWARDED
    assert ledger.formal_submission_eligible is False


def test_synthetic_bid_ledger_rejects_award_after_cancellation():
    ledger = SyntheticBidLedger()
    ledger.submit(bid_id="synthetic-bid-001")
    ledger.cancel(bid_id="synthetic-bid-001")

    with pytest.raises(ValueError, match="cancelled"):
        ledger.record_award(
            bid_id="synthetic-bid-001",
            award_id="synthetic-award-001",
        )


def test_synthetic_market_assets_replay_through_simulation_only_ledger(tmp_path):
    fixture_dir = write_v5_simulation_fixtures(tmp_path, days=1, seed=42)

    ledger = replay_synthetic_market_assets(fixture_dir)

    assert ledger.status_of("synthetic-bid-001") is SyntheticBidStatus.AWARDED
    assert ledger.formal_submission_eligible is False


def test_synthetic_billing_statement_is_permanently_unconfirmed(tmp_path):
    fixture_dir = write_v5_simulation_fixtures(tmp_path, days=1, seed=42)

    statement = load_synthetic_billing_statement(fixture_dir)

    assert statement.statement_version == "synthetic-statement-001"
    assert statement.confirmed is False
    assert statement.lines["simulated_shortfall_charge"] >= 0.0


def test_synthetic_bid_ledger_records_amendment_and_rejection_revisions():
    ledger = SyntheticBidLedger()
    ledger.submit(bid_id="synthetic-bid-002")

    amendment = ledger.amend(bid_id="synthetic-bid-002")
    ledger.reject(bid_id="synthetic-bid-002")

    assert amendment.revision == 2
    assert ledger.status_of("synthetic-bid-002") is SyntheticBidStatus.REJECTED
    assert [event.status for event in ledger.events_for("synthetic-bid-002")] == [
        SyntheticBidStatus.SUBMITTED,
        SyntheticBidStatus.AMENDED,
        SyntheticBidStatus.REJECTED,
    ]
