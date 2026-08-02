"""v5 V5-7：统一经济验收、业务反例门与影子评估。"""

from __future__ import annotations

import numpy as np
import pytest

from ele_trading.backtest.acceptance import (
    ShadowEvaluator,
    block_bootstrap_saving,
    evaluate_acceptance,
)
from ele_trading.backtest.counterexamples import (
    CounterexampleCase,
    CounterexampleSeverity,
)
from ele_trading.markets.shared import DifferenceCategory
from ele_trading.markets.single_settlement.reconciliation import (
    reconcile_single_settlement_statement,
)


def _counterexample(passes: bool) -> CounterexampleCase:
    return CounterexampleCase(
        name="cx-1",
        severity=CounterexampleSeverity.HARD,
        evaluator=lambda context: (passes, {"source": "unit-test"}),
    )


def _clean_reconciliation(confirmed: bool = True):
    modeled = {"energy": 100.0, "contract_difference": 20.0}
    return reconcile_single_settlement_statement(
        modeled=modeled,
        billed=dict(modeled),
        statement_version="stmt-v1",
        tolerance=0.01,
        confirmed=confirmed,
    )


def test_block_bootstrap_detects_consistent_saving():
    rng = np.random.default_rng(1)
    baseline = 1000.0 + rng.normal(0.0, 50.0, 60)
    candidate = baseline - 100.0 + rng.normal(0.0, 10.0, 60)

    result = block_bootstrap_saving(
        baseline, candidate, n_bootstrap=500, seed=3
    )
    assert result.significant
    assert result.mean_saving == pytest.approx(100.0, abs=20.0)
    assert result.ci_low > 0.0


def test_block_bootstrap_rejects_insignificant_difference():
    rng = np.random.default_rng(2)
    baseline = 1000.0 + rng.normal(0.0, 80.0, 60)
    candidate = 1000.0 + rng.normal(0.0, 80.0, 60)

    result = block_bootstrap_saving(
        baseline, candidate, n_bootstrap=500, seed=4
    )
    assert not result.significant
    assert result.ci_low < 0.0 < result.ci_high


def test_acceptance_passes_only_when_all_gates_pass():
    rng = np.random.default_rng(5)
    baseline = 1000.0 + rng.normal(0.0, 30.0, 40)
    candidate = baseline - 80.0

    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        reconciliation_reports=(_clean_reconciliation(),),
        counterexample_cases=(_counterexample(True),),
        no_lookahead=True,
        zero_hard_violations=True,
        bootstrap_kwargs={"n_bootstrap": 500, "seed": 6},
    )
    assert report.passed, report.failures


@pytest.mark.parametrize(
    "kwargs_override, expected_failure",
    [
        ({"counterexample_cases": ()}, "counterexample"),
        ({"reconciliation_reports": ()}, "reconciliation"),
        ({"no_lookahead": None}, "evidence"),
        ({"zero_hard_violations": False}, "evidence"),
    ],
)
def test_acceptance_fails_without_complete_evidence(
    kwargs_override, expected_failure
):
    rng = np.random.default_rng(7)
    baseline = 1000.0 + rng.normal(0.0, 30.0, 40)
    candidate = baseline - 80.0
    kwargs = {
        "reconciliation_reports": (_clean_reconciliation(),),
        "counterexample_cases": (_counterexample(True),),
        "no_lookahead": True,
        "zero_hard_violations": True,
        "bootstrap_kwargs": {"n_bootstrap": 300, "seed": 8},
    }
    kwargs.update(kwargs_override)

    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        **kwargs,
    )
    assert not report.passed
    assert any(expected_failure in item for item in report.failures)


def test_acceptance_risk_gate_rejects_cvar_deterioration():
    baseline = np.full(40, 1000.0)
    candidate = np.full(40, 900.0)
    candidate[-1] = 5000.0  # 均值更省但尾部更差

    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        max_cvar_increase_ratio=0.0,
        reconciliation_reports=(_clean_reconciliation(),),
        counterexample_cases=(_counterexample(True),),
        no_lookahead=True,
        zero_hard_violations=True,
        bootstrap_kwargs={"n_bootstrap": 300, "seed": 9},
    )
    assert not report.passed
    assert not report.gates.risk
    assert report.candidate_cvar > report.baseline_cvar


def test_acceptance_rejects_unconfirmed_reconciliation():
    baseline = np.full(40, 1000.0)
    candidate = np.full(40, 900.0)

    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        reconciliation_reports=(_clean_reconciliation(confirmed=False),),
        counterexample_cases=(_counterexample(True),),
        no_lookahead=True,
        zero_hard_violations=True,
        bootstrap_kwargs={"n_bootstrap": 300, "seed": 10},
    )
    assert not report.passed
    assert not report.gates.reconciliation


def test_acceptance_rejects_failing_hard_counterexample():
    baseline = np.full(40, 1000.0)
    candidate = np.full(40, 900.0)

    report = evaluate_acceptance(
        baseline_losses=baseline,
        candidate_losses=candidate,
        reconciliation_reports=(_clean_reconciliation(),),
        counterexample_cases=(_counterexample(False),),
        no_lookahead=True,
        zero_hard_violations=True,
        bootstrap_kwargs={"n_bootstrap": 300, "seed": 11},
    )
    assert not report.passed
    assert not report.gates.counterexamples
    assert report.counterexample_report is not None
    assert tuple(
        item.name for item in report.counterexample_report.hard_failures
    ) == ("cx-1",)


def test_shadow_evaluator_requires_min_days_then_applies_gates():
    evaluator = ShadowEvaluator(min_days=20)
    for day in range(19):
        evaluator.record_day(baseline_cost=1000.0, candidate_cost=900.0)
    report = evaluator.evaluate()
    assert not report.ready_for_default
    assert "insufficient" in report.reason

    evaluator.record_day(baseline_cost=1000.0, candidate_cost=900.0)
    report = evaluator.evaluate(
        acceptance_kwargs={
            "reconciliation_reports": (_clean_reconciliation(),),
            "counterexample_cases": (_counterexample(True),),
            "no_lookahead": True,
            "zero_hard_violations": True,
            "bootstrap_kwargs": {"n_bootstrap": 300, "seed": 12},
        }
    )
    assert report.ready_for_default, report.reason

    # 候选无增益时即使天数足够也不得切换
    flat = ShadowEvaluator(min_days=20)
    for _ in range(25):
        flat.record_day(baseline_cost=1000.0, candidate_cost=1000.0)
    flat_report = flat.evaluate(
        acceptance_kwargs={
            "reconciliation_reports": (_clean_reconciliation(),),
            "counterexample_cases": (_counterexample(True),),
            "no_lookahead": True,
            "zero_hard_violations": True,
            "bootstrap_kwargs": {"n_bootstrap": 300, "seed": 13},
        }
    )
    assert not flat_report.ready_for_default
    assert flat_report.acceptance is not None
    assert not flat_report.acceptance.gates.statistical
