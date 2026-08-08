"""V5 synthetic runner 的治理消费器。

它只验证 simulation fixture 的 champion/challenger、drift baseline 与 rollback
runbook 能被完整消费；不构成真实影子运行、人工审批或生产晋级证据。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from ele_trading.trading.v5_synthetic_runner import SyntheticV5RunResult


@dataclass(frozen=True, slots=True)
class SyntheticGovernanceReport:
    """Synthetic 治理检查结果；生产资格永久关闭。"""

    champion_version: str
    challenger_version: str
    drift_detected: bool
    total_shortfall_mwh: float
    rollback_dry_run_passed: bool
    production_eligible: bool = False


def _load_synthetic_governance(directory: Path, name: str) -> dict[str, object]:
    payload = json.loads(
        (directory / "governance" / name).read_text(encoding="utf-8")
    )
    if (
        payload.get("environment") != "synthetic-only"
        or payload.get("production_eligible") is not False
    ):
        raise ValueError("synthetic governance assets must remain non-production")
    return payload


def evaluate_synthetic_governance(
    directory: str | Path,
    result: SyntheticV5RunResult,
) -> SyntheticGovernanceReport:
    """消费 runner 产物，计算 drift 并执行无副作用的 rollback dry-run。"""
    root = Path(directory)
    champion = _load_synthetic_governance(root, "champion.yaml")
    challenger = _load_synthetic_governance(root, "challenger.yaml")
    baseline = _load_synthetic_governance(root, "drift_baseline.yaml")
    rollback = _load_synthetic_governance(root, "rollback_runbook.yaml")
    champion_version = champion.get("version")
    challenger_version = challenger.get("version")
    if not isinstance(champion_version, str) or not champion_version:
        raise ValueError("synthetic champion version is required")
    if not isinstance(challenger_version, str) or not challenger_version:
        raise ValueError("synthetic challenger version is required")
    threshold_raw = baseline.get("max_total_shortfall_mwh")
    if isinstance(threshold_raw, bool) or not isinstance(
        threshold_raw,
        (int, float),
    ):
        raise ValueError("synthetic drift threshold must be numeric")
    threshold = float(threshold_raw)
    if not np.isfinite(threshold) or threshold < 0.0:
        raise ValueError("synthetic drift threshold must be finite and non-negative")
    total_shortfall = float(
        sum(item.shortfall_mwh for item in result.resource_execution_deviations)
    )
    target = rollback.get("rollback_target")
    rollback_passed = (
        isinstance(target, str)
        and target == "synthetic champion fixture"
        and bool(champion_version)
    )
    return SyntheticGovernanceReport(
        champion_version=champion_version,
        challenger_version=challenger_version,
        drift_detected=bool(total_shortfall > threshold),
        total_shortfall_mwh=total_shortfall,
        rollback_dry_run_passed=rollback_passed,
    )
