"""V5 synthetic 资产的端到端运行边界。"""

from __future__ import annotations

from ele_trading.trading.synthetic.acceptance import evaluate_synthetic_run
from ele_trading.trading.synthetic.evidence import summarize_synthetic_run
from ele_trading.trading.synthetic.fixtures import write_synthetic_fixtures
from ele_trading.trading.synthetic.governance import evaluate_synthetic_governance
from ele_trading.trading.synthetic.runner import run_synthetic


def test_synthetic_runner_consumes_plan_metering_and_market_assets(tmp_path):
    fixture_dir = write_synthetic_fixtures(tmp_path, days=1, seed=42)

    result = run_synthetic(fixture_dir)

    assert result.ledger.status_of("synthetic-bid-001").value == "awarded"
    assert result.ledger.status_of("synthetic-bid-002").value == "rejected"
    assert result.ledger.status_of("synthetic-bid-003").value == "cancelled"
    assert len(result.ledger.events_for("synthetic-bid-002")) == 3
    assert {item.resource_id for item in result.resource_execution_deviations} == {
        "bess-a",
        "bess-b",
    }
    assert result.billing_statement.confirmed is False
    assert result.formal_acceptance_eligible is False
    assert result.production_eligible is False


def test_synthetic_governance_consumes_drift_and_rollback_assets(tmp_path):
    fixture_dir = write_synthetic_fixtures(tmp_path, days=1, seed=42)

    report = evaluate_synthetic_governance(
        fixture_dir,
        run_synthetic(fixture_dir),
    )

    assert report.champion_version == "synthetic-champion-v1"
    assert report.challenger_version == "synthetic-challenger-v1"
    assert report.drift_detected is False
    assert report.rollback_dry_run_passed is True
    assert report.production_eligible is False


def test_synthetic_run_evidence_is_auditable_but_not_formal_acceptance(tmp_path):
    fixture_dir = write_synthetic_fixtures(tmp_path, days=1, seed=42)

    evidence = summarize_synthetic_run(fixture_dir, run_synthetic(fixture_dir))

    assert evidence.source_id == "v5_synthetic_fixture"
    assert evidence.resource_count == 2
    assert evidence.unconfirmed_billing is True
    assert evidence.formal_acceptance_eligible is False


def test_synthetic_acceptance_unifies_engineering_checks_without_formal_promotion(tmp_path):
    fixture_dir = write_synthetic_fixtures(tmp_path, days=1, seed=42)

    report = evaluate_synthetic_run(fixture_dir)

    assert report.engineering_checks_passed is True
    assert report.billing_reconciliation.confirmed is False
    assert report.governance.rollback_dry_run_passed is True
    assert report.evidence.formal_acceptance_eligible is False
    assert report.formal_acceptance_eligible is False
    assert report.production_eligible is False
