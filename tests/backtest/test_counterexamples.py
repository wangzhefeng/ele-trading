"""v5 V5-0：业务反例注册与 HARD 验收。"""

from __future__ import annotations

from ele_trading.backtest.counterexamples import (
    CounterexampleCase,
    CounterexampleSeverity,
    run_counterexamples,
)


def test_hard_failure_marks_entire_report_failed():
    report = run_counterexamples(
        (
            CounterexampleCase(
                name="no_lookahead",
                severity=CounterexampleSeverity.HARD,
                evaluator=lambda context: (
                    context["feature_as_of"] <= context["decision_time"],
                    {"feature_as_of": context["feature_as_of"]},
                ),
            ),
        ),
        {"feature_as_of": 2, "decision_time": 1},
    )

    assert not report.passed
    assert report.hard_failures[0].name == "no_lookahead"
    assert report.hard_failures[0].evidence == {"feature_as_of": 2}


def test_soft_failure_is_reported_without_failing_hard_gate():
    report = run_counterexamples(
        (
            CounterexampleCase(
                name="calibration_warning",
                severity=CounterexampleSeverity.SOFT,
                evaluator=lambda _context: (False, {"coverage_gap": 0.03}),
            ),
        ),
        {},
    )

    assert report.passed
    assert report.soft_failures[0].name == "calibration_warning"


def test_evaluator_exception_becomes_explicit_failed_evidence():
    def broken(_context):
        raise RuntimeError("solver exploded")

    report = run_counterexamples(
        (
            CounterexampleCase(
                name="solver_failure",
                severity=CounterexampleSeverity.HARD,
                evaluator=broken,
            ),
        ),
        {},
    )

    assert not report.passed
    assert report.hard_failures[0].evidence == {
        "exception_type": "RuntimeError",
        "exception_message": "solver exploded",
    }


def test_duplicate_counterexample_names_are_rejected():
    case = CounterexampleCase(
        name="duplicate",
        severity=CounterexampleSeverity.HARD,
        evaluator=lambda _context: (True, {}),
    )

    try:
        run_counterexamples((case, case), {})
    except ValueError as exc:
        assert str(exc) == "counterexample names must be unique"
    else:
        raise AssertionError("duplicate counterexample names must fail")
