"""消费 V5 synthetic fixture 的只读工程 runner。

该 runner 验证仿真资产的版本、时标、计划—计量和模拟市场回放关系。它明确
不生成正式验收证据，也不允许生产使用。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from ele_trading.domain.contracts import (
    BillingStatement,
    ResourceExecutionDeviation,
    ResourceMetering,
)
from ele_trading.trading.synthetic.market import (
    SyntheticBidLedger,
    load_synthetic_billing_statement,
    replay_synthetic_market_assets,
)


@dataclass(frozen=True, slots=True)
class SyntheticRunResult:
    """Synthetic runner 产物；两个 eligibility 字段永久为 False。"""

    ledger: SyntheticBidLedger
    resource_execution_deviations: tuple[ResourceExecutionDeviation, ...]
    billing_statement: BillingStatement
    formal_acceptance_eligible: bool = False
    production_eligible: bool = False


def _read_manifest(directory: Path) -> dict[str, object]:
    manifest = json.loads((directory / "manifest.yaml").read_text(encoding="utf-8"))
    if (
        manifest.get("source_id") != "v5_synthetic_fixture"
        or manifest.get("quality_flag") != "synthetic"
        or manifest.get("production_eligible") is not False
        or manifest.get("formal_billing_eligible") is not False
    ):
        raise ValueError("synthetic runner requires a non-production synthetic manifest")
    return manifest


def _resource_metering(frame: pd.DataFrame, resource_id: str) -> ResourceMetering:
    resource_rows = frame[frame["resource_id"] == resource_id].copy()
    if resource_rows.empty or set(resource_rows["quality_flag"]) != {"synthetic"}:
        raise ValueError("synthetic metering must cover each resource with synthetic rows")
    index = pd.DatetimeIndex(pd.to_datetime(resource_rows["event_time"], utc=True))
    if index.has_duplicates:
        raise ValueError("synthetic metering intervals must be unique")
    interval_energy = pd.Series(
        resource_rows["actual_discharge_energy_mwh"].to_numpy(dtype=float),
        index=index,
    )
    observed_at = pd.Timestamp(resource_rows["available_at"].iloc[-1])
    return ResourceMetering(
        resource_id=resource_id,
        observed_at=observed_at,
        interval_discharge_mwh=interval_energy,
        source_version=str(resource_rows["source_version"].iloc[-1]),
    )


def run_synthetic(directory: str | Path) -> SyntheticRunResult:
    """运行 simulation-only 的计划—计量—市场回放检查。"""
    root = Path(directory)
    _read_manifest(root)
    plans = pd.read_csv(root / "plans" / "day_ahead_plan.csv")
    metering_frame = pd.read_csv(root / "metering" / "resource_metering.csv")
    if set(plans["quality_flag"]) != {"synthetic"}:
        raise ValueError("synthetic plans must be marked synthetic")

    deviations: list[ResourceExecutionDeviation] = []
    for resource_id in sorted(set(plans["resource_id"])):
        planned_rows = plans[plans["resource_id"] == resource_id].copy()
        planned_index = pd.DatetimeIndex(
            pd.to_datetime(planned_rows["interval_start"], utc=True)
        )
        planned = pd.Series(
            planned_rows["planned_discharge_energy_mwh"].to_numpy(dtype=float),
            index=planned_index,
        )
        deviations.append(
            ResourceExecutionDeviation.from_planned_discharge(
                resource_id=str(resource_id),
                planned_interval_discharge_mwh=planned,
                metering=_resource_metering(metering_frame, str(resource_id)),
                plan_version=str(planned_rows["plan_version"].iloc[-1]),
            )
        )

    return SyntheticRunResult(
        ledger=replay_synthetic_market_assets(root),
        resource_execution_deviations=tuple(deviations),
        billing_statement=load_synthetic_billing_statement(root),
    )
