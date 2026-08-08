"""Synthetic V5 工程检查汇总，不是正式验收。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ele_trading.markets.shared import ReconciliationReport
from ele_trading.trading.v5_synthetic_billing import reconcile_synthetic_billing
from ele_trading.trading.v5_synthetic_evidence import (
    SyntheticRunEvidence,
    summarize_synthetic_run,
)
from ele_trading.trading.v5_synthetic_governance import (
    SyntheticGovernanceReport,
    evaluate_synthetic_governance,
)
from ele_trading.trading.v5_synthetic_runner import run_synthetic_v5
from ele_trading.trading.v5_synthetic_market import SyntheticBidStatus


@dataclass(frozen=True, slots=True)
class SyntheticAcceptanceReport:
    """统一的工程自检结果；不具备正式验收或生产资格。"""

    engineering_checks_passed: bool
    billing_reconciliation: ReconciliationReport
    governance: SyntheticGovernanceReport
    evidence: SyntheticRunEvidence
    formal_acceptance_eligible: bool = False
    production_eligible: bool = False


def evaluate_synthetic_run(directory: str | Path) -> SyntheticAcceptanceReport:
    """运行并汇总 synthetic-only 市场、计量、账单和治理检查。"""
    root = Path(directory)
    result = run_synthetic_v5(root)
    billing = reconcile_synthetic_billing(root)
    governance = evaluate_synthetic_governance(root, result)
    evidence = summarize_synthetic_run(root, result)
    terminal = all(
        result.ledger.status_of(bid_id)
        in {
            SyntheticBidStatus.AWARDED,
            SyntheticBidStatus.REJECTED,
            SyntheticBidStatus.CANCELLED,
        }
        for bid_id in result.ledger.bid_ids()
    )
    engineering_checks_passed = bool(
        terminal
        and not billing.confirmed
        and not governance.drift_detected
        and governance.rollback_dry_run_passed
        and evidence.unconfirmed_billing
        and not result.formal_acceptance_eligible
        and not result.production_eligible
    )
    return SyntheticAcceptanceReport(
        engineering_checks_passed=engineering_checks_passed,
        billing_reconciliation=billing,
        governance=governance,
        evidence=evidence,
    )
