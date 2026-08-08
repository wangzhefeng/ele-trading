"""统一经济验收与影子评估（v5 §12 / §14 默认切换门）。

候选策略晋级必须同时通过：

1. 统计门：结算成本相对基线的 block-bootstrap 置信区间显著为正；
2. 风险门：CVaR 与尾部损失不恶化（容差外拒绝）；
3. 对账门：所有正式账单 ReconciliationReport 通过（confirmed 且零差异）；
4. 反例门：全部 HARD 业务反例通过；
5. 前置不变量：无前瞻、零硬约束违约（由调用方以标志位申报，缺失即失败）。

任何一门失败即整体失败；不存在"统计过、业务豁免"的路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from ele_trading.markets.shared import ReconciliationReport
from ele_trading.optimization.risk import weighted_var_cvar

from .counterexamples import CounterexampleReport, run_counterexamples


# ------------------------------------------------------------------ #
#  Block bootstrap 显著性
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """block bootstrap 的节省（baseline − candidate）分布摘要。"""

    mean_saving: float
    ci_low: float
    ci_high: float
    significant: bool
    n_bootstrap: int
    block_length: int


def block_bootstrap_saving(
    baseline_losses: Sequence[float],
    candidate_losses: Sequence[float],
    *,
    block_length: int = 5,
    n_bootstrap: int = 1_000,
    alpha: float = 0.05,
    seed: int = 7,
) -> BootstrapResult:
    """对日成本差（baseline − candidate）做环形 block bootstrap。

    ``significant`` 要求下置信界严格大于 0（候选显著优于基线）。
    """
    baseline = np.asarray(baseline_losses, dtype=float)
    candidate = np.asarray(candidate_losses, dtype=float)
    if baseline.shape != candidate.shape or baseline.ndim != 1 or not len(baseline):
        raise ValueError("loss series must be aligned and non-empty")
    if not np.isfinite(baseline).all() or not np.isfinite(candidate).all():
        raise ValueError("loss series must be finite")
    if not isinstance(block_length, int) or not 1 <= block_length <= len(baseline):
        raise ValueError("block_length must be within [1, len(series)]")
    if not isinstance(n_bootstrap, int) or n_bootstrap < 100:
        raise ValueError("n_bootstrap must be an integer >= 100")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be within (0, 1)")

    diff = baseline - candidate
    n = len(diff)
    n_blocks = int(np.ceil(n / block_length))
    rng = np.random.default_rng(int(seed))
    means = np.empty(n_bootstrap)
    for replication in range(n_bootstrap):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.concatenate(
            [
                np.asarray(
                    [diff[(start + offset) % n] for offset in range(block_length)]
                )
                for start in starts
            ]
        )[:n]
        means[replication] = sample.mean()
    ci_low, ci_high = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return BootstrapResult(
        mean_saving=float(diff.mean()),
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        significant=bool(ci_low > 0.0),
        n_bootstrap=n_bootstrap,
        block_length=block_length,
    )


# ------------------------------------------------------------------ #
#  统一验收门
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class InvariantEvidence:
    """由运行器产出的前置不变量证据（正式验收的唯一接受形式）。

    调用方手工申报的布尔标志不构成正式证据；验收只信任运行期间
    实际执行的检查计数与决策追踪。
    """

    no_lookahead_checks: int        # 运行期间执行的防前瞻断言次数（>0 才有效）
    hard_constraint_violations: int # 硬约束违约计数（必须为 0）
    decision_traces: tuple[Any, ...]  # 运行产出的 DecisionTrace（非空）

    def __post_init__(self) -> None:
        for name in ("no_lookahead_checks", "hard_constraint_violations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class AcceptanceGates:
    """逐项门结果。"""

    statistical: bool
    risk: bool
    reconciliation: bool
    counterexamples: bool
    invariants: bool


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    """经济验收总报告：全部门通过才 passed。"""

    passed: bool
    gates: AcceptanceGates
    bootstrap: BootstrapResult
    baseline_cvar: float
    candidate_cvar: float
    counterexample_report: CounterexampleReport | None
    failures: tuple[str, ...]


def evaluate_acceptance(
    *,
    baseline_losses: Sequence[float],
    candidate_losses: Sequence[float],
    cvar_alpha: float = 0.95,
    max_cvar_increase_ratio: float = 0.0,
    reconciliation_reports: Sequence[ReconciliationReport] = (),
    counterexample_cases: Sequence = (),
    invariant_evidence: InvariantEvidence | None = None,
    no_lookahead: bool | None = None,
    zero_hard_violations: bool | None = None,
    bootstrap_kwargs: Mapping[str, object] | None = None,
) -> AcceptanceReport:
    """执行统一经济验收。

    正式验收只接受 ``invariant_evidence``；``no_lookahead`` /
    ``zero_hard_violations`` 是调用方申报的遗留入口，不能使
    invariants 门通过——缺失或仅有申报均按失败处理。
    """
    if not 0.0 < cvar_alpha < 1.0:
        raise ValueError("cvar_alpha must be within (0, 1)")
    if not np.isfinite(max_cvar_increase_ratio) or max_cvar_increase_ratio < 0.0:
        raise ValueError("max_cvar_increase_ratio must be finite and non-negative")

    failures: list[str] = []

    bootstrap = block_bootstrap_saving(
        baseline_losses,
        candidate_losses,
        **dict(bootstrap_kwargs or {}),
    )
    statistical = bootstrap.significant
    if not statistical:
        failures.append("cost saving is not statistically significant")

    baseline = np.asarray(baseline_losses, dtype=float)
    candidate = np.asarray(candidate_losses, dtype=float)
    _, baseline_cvar = weighted_var_cvar(
        {f"b{i}": float(loss) for i, loss in enumerate(baseline)},
        {f"b{i}": 1.0 / len(baseline) for i in range(len(baseline))},
        alpha=cvar_alpha,
    )
    _, candidate_cvar = weighted_var_cvar(
        {f"c{i}": float(loss) for i, loss in enumerate(candidate)},
        {f"c{i}": 1.0 / len(candidate) for i in range(len(candidate))},
        alpha=cvar_alpha,
    )
    risk = candidate_cvar <= baseline_cvar * (1.0 + max_cvar_increase_ratio) + 1e-9
    if not risk:
        failures.append("candidate CVaR deteriorates beyond tolerance")

    reconciliation = bool(reconciliation_reports) and all(
        report.passed for report in reconciliation_reports
    )
    if not reconciliation:
        failures.append("reconciliation reports missing or failing")

    counterexample_report: CounterexampleReport | None = None
    if counterexample_cases:
        counterexample_report = run_counterexamples(
            tuple(counterexample_cases),
            {},
        )
    counterexamples = (
        counterexample_report is not None and counterexample_report.passed
    )
    if not counterexamples:
        failures.append("counterexample suite missing or failing")

    if invariant_evidence is None:
        invariants = False
        failures.append(
            "invariant evidence missing: caller-declared flags are not "
            "formal evidence"
        )
    else:
        invariants = (
            invariant_evidence.no_lookahead_checks > 0
            and invariant_evidence.hard_constraint_violations == 0
            and bool(invariant_evidence.decision_traces)
        )
        if not invariants:
            failures.append(
                "invariant evidence invalid: requires no-lookahead checks, "
                "zero hard violations and non-empty decision traces"
            )

    gates = AcceptanceGates(
        statistical=statistical,
        risk=risk,
        reconciliation=reconciliation,
        counterexamples=counterexamples,
        invariants=invariants,
    )
    return AcceptanceReport(
        passed=all(getattr(gates, item.name) for item in fields(gates)),
        gates=gates,
        bootstrap=bootstrap,
        baseline_cvar=float(baseline_cvar),
        candidate_cvar=float(candidate_cvar),
        counterexample_report=counterexample_report,
        failures=tuple(failures),
    )


# ------------------------------------------------------------------ #
#  影子评估
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class ShadowReport:
    """影子运行累计证据与默认切换结论。"""

    days: int
    acceptance: AcceptanceReport | None
    ready_for_default: bool
    reason: str


class ShadowEvaluator:
    """按日累计候选/基线成本，满足样本量后执行统一验收。"""

    def __init__(self, *, min_days: int = 20) -> None:
        if not isinstance(min_days, int) or min_days < 2:
            raise ValueError("min_days must be an integer >= 2")
        self.min_days = min_days
        self._baseline: list[float] = []
        self._candidate: list[float] = []

    def record_day(self, *, baseline_cost: float, candidate_cost: float) -> None:
        for name, amount in (
            ("baseline_cost", baseline_cost),
            ("candidate_cost", candidate_cost),
        ):
            if not np.isfinite(float(amount)):
                raise ValueError(f"{name} must be finite")
        self._baseline.append(float(baseline_cost))
        self._candidate.append(float(candidate_cost))

    @property
    def days(self) -> int:
        return len(self._baseline)

    def evaluate(
        self,
        *,
        acceptance_kwargs: Mapping[str, Any] | None = None,
    ) -> ShadowReport:
        if self.days < self.min_days:
            return ShadowReport(
                days=self.days,
                acceptance=None,
                ready_for_default=False,
                reason=f"insufficient shadow days: {self.days} < {self.min_days}",
            )
        acceptance = evaluate_acceptance(
            baseline_losses=self._baseline,
            candidate_losses=self._candidate,
            **dict(acceptance_kwargs or {}),
        )
        return ShadowReport(
            days=self.days,
            acceptance=acceptance,
            ready_for_default=acceptance.passed,
            reason=(
                "all acceptance gates passed"
                if acceptance.passed
                else "; ".join(acceptance.failures)
            ),
        )
