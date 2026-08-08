"""由 synthetic runner 产物派生的非正式运行证据摘要。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from ele_trading.trading.v5_synthetic_runner import SyntheticV5RunResult


@dataclass(frozen=True, slots=True)
class SyntheticRunEvidence:
    """可审计的 simulation-only 摘要，不是正式验收证据。"""

    source_id: str
    resource_count: int
    total_shortfall_mwh: float
    unconfirmed_billing: bool
    formal_acceptance_eligible: bool = False


def summarize_synthetic_run(
    directory: str | Path,
    result: SyntheticV5RunResult,
) -> SyntheticRunEvidence:
    """从 runner 真实输出派生审计摘要，拒绝非 synthetic manifest。"""
    manifest = json.loads(
        (Path(directory) / "manifest.yaml").read_text(encoding="utf-8")
    )
    source_id = manifest.get("source_id")
    if (
        not isinstance(source_id, str)
        or manifest.get("quality_flag") != "synthetic"
        or manifest.get("production_eligible") is not False
        or manifest.get("formal_billing_eligible") is not False
    ):
        raise ValueError("synthetic evidence requires a non-production manifest")
    return SyntheticRunEvidence(
        source_id=source_id,
        resource_count=len(result.resource_execution_deviations),
        total_shortfall_mwh=float(
            sum(item.shortfall_mwh for item in result.resource_execution_deviations)
        ),
        unconfirmed_billing=not result.billing_statement.confirmed,
    )
