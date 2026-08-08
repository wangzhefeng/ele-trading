"""V6-0 场景准入：诊断通过后才允许进入优化。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from ele_trading.scenario.diagnostics import ScenarioDiagnostics


class ScenarioEvidenceTier(StrEnum):
    """场景结果可使用的证据层级。"""

    SYNTHETIC = "synthetic"
    RESEARCH = "research"
    REAL_CANDIDATE = "real_candidate"
    SHADOW = "shadow"
    PRODUCTION = "production"


class ScenarioAdmissionStatus(StrEnum):
    """场景集在当前证据层级的准入结论。"""

    ADMITTED = "admitted"
    DEGRADED = "degraded"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ScenarioAdmissionDecision:
    """可审计的场景准入结论；不以布尔值掩盖降级或拒绝原因。"""

    evidence_tier: ScenarioEvidenceTier
    status: ScenarioAdmissionStatus
    failed_checks: tuple[str, ...]
    degraded_checks: tuple[str, ...]
    stage: str | None = None

    @property
    def admitted(self) -> bool:
        return self.status is not ScenarioAdmissionStatus.REJECTED

    def for_stage(self, stage: str) -> "ScenarioAdmissionDecision":
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError("stage must be non-empty")
        return replace(self, stage=stage)


class ScenarioAdmissionRejected(RuntimeError):
    """场景未准入时阻断候选优化，防止静默继续求解。"""

    def __init__(self, decision: ScenarioAdmissionDecision) -> None:
        self.decision = decision
        stage = f" at {decision.stage}" if decision.stage is not None else ""
        failed = ", ".join(decision.failed_checks) or "unknown"
        super().__init__(
            "scenario admission rejected"
            f"{stage} for {decision.evidence_tier.value}: {failed}"
        )


@dataclass(frozen=True, slots=True)
class ScenarioAdmissionPolicy:
    """按证据层级解释既有场景诊断，保留显式研究降级。"""

    evidence_tier: ScenarioEvidenceTier

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_tier, ScenarioEvidenceTier):
            raise ValueError("evidence_tier must be a ScenarioEvidenceTier")

    def evaluate(
        self,
        diagnostics: ScenarioDiagnostics,
    ) -> ScenarioAdmissionDecision:
        """将诊断报告映射为 admitted/degraded/rejected。"""
        failed_checks = diagnostics.failed_checks
        skipped_checks = tuple(
            check.name
            for check in diagnostics.checks
            if check.detail.startswith("skipped:")
        )
        if failed_checks:
            if self.evidence_tier in {
                ScenarioEvidenceTier.SYNTHETIC,
                ScenarioEvidenceTier.RESEARCH,
            }:
                return ScenarioAdmissionDecision(
                    evidence_tier=self.evidence_tier,
                    status=ScenarioAdmissionStatus.DEGRADED,
                    failed_checks=failed_checks,
                    degraded_checks=failed_checks + skipped_checks,
                )
            return ScenarioAdmissionDecision(
                evidence_tier=self.evidence_tier,
                status=ScenarioAdmissionStatus.REJECTED,
                failed_checks=failed_checks,
                degraded_checks=(),
            )
        if skipped_checks:
            if self.evidence_tier in {
                ScenarioEvidenceTier.SYNTHETIC,
                ScenarioEvidenceTier.RESEARCH,
            }:
                return ScenarioAdmissionDecision(
                    evidence_tier=self.evidence_tier,
                    status=ScenarioAdmissionStatus.DEGRADED,
                    failed_checks=(),
                    degraded_checks=skipped_checks,
                )
            return ScenarioAdmissionDecision(
                evidence_tier=self.evidence_tier,
                status=ScenarioAdmissionStatus.REJECTED,
                failed_checks=skipped_checks,
                degraded_checks=(),
            )
        return ScenarioAdmissionDecision(
            evidence_tier=self.evidence_tier,
            status=ScenarioAdmissionStatus.ADMITTED,
            failed_checks=(),
            degraded_checks=(),
        )
