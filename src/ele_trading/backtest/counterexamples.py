"""业务反例的注册、执行与 HARD 门报告。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping


class CounterexampleSeverity(str, Enum):
    """HARD 失败阻断回测，SOFT 失败只进入诊断。"""

    HARD = "hard"
    SOFT = "soft"


CounterexampleEvaluator = Callable[
    [Mapping[str, object]],
    tuple[bool, Mapping[str, object]],
]


@dataclass(frozen=True, slots=True)
class CounterexampleCase:
    """一个可复用的业务反例检查。"""

    name: str
    severity: CounterexampleSeverity
    evaluator: CounterexampleEvaluator

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must not be empty")
        if not isinstance(self.severity, CounterexampleSeverity):
            raise ValueError("severity must be a CounterexampleSeverity")
        if not callable(self.evaluator):
            raise ValueError("evaluator must be callable")


@dataclass(frozen=True, slots=True)
class CounterexampleResult:
    """单项反例执行结果及可复算证据。"""

    name: str
    passed: bool
    severity: CounterexampleSeverity
    evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must not be empty")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a boolean")
        if not isinstance(self.severity, CounterexampleSeverity):
            raise ValueError("severity must be a CounterexampleSeverity")
        if not isinstance(self.evidence, Mapping):
            raise ValueError("evidence must be a mapping")
        object.__setattr__(self, "evidence", dict(self.evidence))


@dataclass(frozen=True, slots=True)
class CounterexampleReport:
    """一次反例集执行结果；只有 HARD 失败会阻断。"""

    results: tuple[CounterexampleResult, ...]

    def __post_init__(self) -> None:
        results = tuple(self.results)
        if not all(isinstance(item, CounterexampleResult) for item in results):
            raise ValueError("results must contain CounterexampleResult objects")
        names = tuple(item.name for item in results)
        if len(names) != len(set(names)):
            raise ValueError("counterexample names must be unique")
        object.__setattr__(self, "results", results)

    @property
    def hard_failures(self) -> tuple[CounterexampleResult, ...]:
        return tuple(
            item
            for item in self.results
            if not item.passed and item.severity is CounterexampleSeverity.HARD
        )

    @property
    def soft_failures(self) -> tuple[CounterexampleResult, ...]:
        return tuple(
            item
            for item in self.results
            if not item.passed and item.severity is CounterexampleSeverity.SOFT
        )

    @property
    def passed(self) -> bool:
        return not self.hard_failures


def run_counterexamples(
    cases: tuple[CounterexampleCase, ...],
    context: Mapping[str, object],
) -> CounterexampleReport:
    """执行反例集；检查器异常转成显式失败证据而非中断报告。"""
    cases = tuple(cases)
    names = tuple(case.name for case in cases)
    if len(names) != len(set(names)):
        raise ValueError("counterexample names must be unique")
    if not isinstance(context, Mapping):
        raise ValueError("context must be a mapping")

    results: list[CounterexampleResult] = []
    for case in cases:
        try:
            passed, evidence = case.evaluator(context)
            if not isinstance(passed, bool):
                raise ValueError("counterexample evaluator must return a boolean")
            if not isinstance(evidence, Mapping):
                raise ValueError("counterexample evaluator evidence must be a mapping")
            result = CounterexampleResult(
                name=case.name,
                passed=passed,
                severity=case.severity,
                evidence=evidence,
            )
        except Exception as exc:  # 反例执行失败本身就是显式失败证据
            result = CounterexampleResult(
                name=case.name,
                passed=False,
                severity=case.severity,
                evidence={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
        results.append(result)
    return CounterexampleReport(tuple(results))
