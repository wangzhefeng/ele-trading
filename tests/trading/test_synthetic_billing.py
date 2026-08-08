"""V5 synthetic 规则化账单的边界。"""

from __future__ import annotations

from ele_trading.trading.synthetic.fixtures import write_synthetic_fixtures
from ele_trading.trading.synthetic.billing import (
    calculate_synthetic_billing,
    reconcile_synthetic_billing,
)


def test_synthetic_billing_is_computed_from_rule_award_and_metering(tmp_path):
    fixture_dir = write_synthetic_fixtures(tmp_path, days=1, seed=42)

    statement = calculate_synthetic_billing(fixture_dir)

    assert statement.statement_id == "synthetic-statement-001"
    assert statement.revision == 1
    assert statement.rule_version == "synthetic-v1"
    assert statement.confirmed is False
    assert statement.shortfall_mwh >= 0.0
    assert statement.simulated_shortfall_charge == (
        statement.shortfall_mwh * 100.0
    )


def test_synthetic_billing_reconciliation_cannot_pass_formal_acceptance(tmp_path):
    fixture_dir = write_synthetic_fixtures(tmp_path, days=1, seed=42)

    report = reconcile_synthetic_billing(fixture_dir)

    assert report.confirmed is False
    assert report.passed is False
    assert report.differences == ()
