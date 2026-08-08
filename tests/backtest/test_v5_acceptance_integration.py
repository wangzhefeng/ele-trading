"""V5-9 Task 13：验收证据对象替换调用方申报 bool。"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd
import pytest

from ele_trading.backtest.acceptance import (
    InvariantEvidence,
    evaluate_acceptance,
)
from ele_trading.backtest.counterexamples import (
    CounterexampleCase,
    CounterexampleSeverity,
)
from ele_trading.domain.contracts import DecisionTrace
from ele_trading.markets.single_settlement.reconciliation import (
    reconcile_single_settlement_statement,
)

DECISION_TIME = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai"),
)


def _trace() -> DecisionTrace:
    return DecisionTrace(
        decision_time=DECISION_TIME,
        input_versions={"price": "price-v1"},
        model_versions={"dispatch": "dispatch-v1"},
        config_version="config-v1",
        solver_name="CBC",
        solver_version="2.10",
        solver_status="optimal",
    )


def _evidence(**overrides) -> InvariantEvidence:
    values = {
        "no_lookahead_checks": 4,
        "hard_constraint_violations": 0,
        "decision_traces": (_trace(),),
    }
    values.update(overrides)
    return InvariantEvidence(**values)


def _counterexample(passes: bool = True) -> CounterexampleCase:
    return CounterexampleCase(
        name="cx-1",
        severity=CounterexampleSeverity.HARD,
        evaluator=lambda context: (passes, {"source": "unit-test"}),
    )


def _clean_reconciliation():
    modeled = {"energy": 100.0, "contract_difference": 20.0}
    return reconcile_single_settlement_statement(
        modeled=modeled,
        billed=dict(modeled),
        statement_version="stmt-v1",
        tolerance=0.01,
        confirmed=True,
    )


def _losses():
    rng = np.random.default_rng(5)
    baseline = 1000.0 + rng.normal(0.0, 30.0, 40)
    return baseline, baseline - 80.0


def test_legacy_caller_declared_flags_are_not_formal_evidence():
    """只申报 bool 而没有 InvariantEvidence：正式验收必须失败。"""
    baseline, candidate = _losses()
    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        reconciliation_reports=(_clean_reconciliation(),),
        counterexample_cases=(_counterexample(),),
        no_lookahead=True,
        zero_hard_violations=True,
        bootstrap_kwargs={"n_bootstrap": 300, "seed": 6},
    )
    assert not report.passed
    assert not report.gates.invariants
    assert any("formal evidence" in item for item in report.failures)


@pytest.mark.parametrize(
    "overrides",
    [
        {"no_lookahead_checks": 0},
        {"hard_constraint_violations": 1},
        {"decision_traces": ()},
    ],
)
def test_invalid_invariant_evidence_fails_formal_gate(overrides):
    baseline, candidate = _losses()
    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        reconciliation_reports=(_clean_reconciliation(),),
        counterexample_cases=(_counterexample(),),
        invariant_evidence=_evidence(**overrides),
        bootstrap_kwargs={"n_bootstrap": 300, "seed": 7},
    )
    assert not report.passed
    assert not report.gates.invariants


def test_valid_invariant_evidence_passes_when_all_gates_pass():
    baseline, candidate = _losses()
    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        reconciliation_reports=(_clean_reconciliation(),),
        counterexample_cases=(_counterexample(),),
        invariant_evidence=_evidence(),
        bootstrap_kwargs={"n_bootstrap": 300, "seed": 8},
    )
    assert report.passed, report.failures
