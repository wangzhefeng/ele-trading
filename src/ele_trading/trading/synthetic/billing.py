"""基于 explicit synthetic 规则的模拟账单计算与非正式对账。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd

from ele_trading.markets.shared import ReconciliationReport, reconcile_statement_lines


@dataclass(frozen=True, slots=True)
class SyntheticBillingStatement:
    """Simulation-only 账单，永远不能作为正式结算依据。"""

    statement_id: str
    revision: int
    rule_version: str
    award_id: str
    awarded_mwh: float
    metered_mwh: float
    shortfall_mwh: float
    simulated_shortfall_charge: float
    confirmed: bool = False


def _load_rule(root: Path) -> tuple[str, float]:
    rule = json.loads(
        (root / "settlement" / "settlement_rule_cases.yaml").read_text(
            encoding="utf-8"
        )
    )
    if rule.get("quality_flag") != "synthetic" or rule.get("confirmed") is not False:
        raise ValueError("synthetic settlement rule must be unconfirmed and synthetic")
    version = rule.get("rule_version")
    rate = rule.get("shortfall_charge_cny_per_mwh")
    if not isinstance(version, str) or not version:
        raise ValueError("synthetic settlement rule version is required")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate < 0:
        raise ValueError("synthetic shortfall charge must be non-negative")
    return version, float(rate)


def calculate_synthetic_billing(directory: str | Path) -> SyntheticBillingStatement:
    """仅由 synthetic Award、外部计量与规则计算账单分项。"""
    root = Path(directory)
    rule_version, rate = _load_rule(root)
    awards = pd.read_csv(root / "market" / "award_receipts.csv")
    if len(awards) != 1 or set(awards["quality_flag"]) != {"synthetic"}:
        raise ValueError("synthetic billing requires exactly one synthetic award")
    award = awards.iloc[0]
    resource_id = str(award["resource_id"])
    metering = pd.read_csv(root / "metering" / "resource_metering.csv")
    resource_rows = metering[metering["resource_id"] == resource_id]
    if resource_rows.empty or set(resource_rows["quality_flag"]) != {"synthetic"}:
        raise ValueError("synthetic billing requires synthetic resource metering")
    awarded_mwh = float(award["cleared_quantity_mwh"])
    metered_mwh = float(resource_rows["actual_discharge_energy_mwh"].sum())
    shortfall_mwh = max(0.0, awarded_mwh - metered_mwh)
    fixture = pd.read_csv(root / "settlement" / "simulated_billing_statement.csv")
    if len(fixture) != 1:
        raise ValueError("synthetic billing fixture must contain one statement")
    fixture_row = fixture.iloc[0]
    return SyntheticBillingStatement(
        statement_id=str(fixture_row["statement_id"]),
        revision=int(fixture_row.get("revision", 1)),
        rule_version=rule_version,
        award_id=str(award["award_id"]),
        awarded_mwh=awarded_mwh,
        metered_mwh=metered_mwh,
        shortfall_mwh=shortfall_mwh,
        simulated_shortfall_charge=shortfall_mwh * rate,
    )


def reconcile_synthetic_billing(directory: str | Path) -> ReconciliationReport:
    """与 fixture 对账；即使一致也保持 confirmed=False。"""
    root = Path(directory)
    calculated = calculate_synthetic_billing(root)
    fixture = pd.read_csv(root / "settlement" / "simulated_billing_statement.csv")
    row = fixture.iloc[0]
    if str(row["rule_version"]) != calculated.rule_version:
        raise ValueError("synthetic billing fixture rule version does not match rule")
    return reconcile_statement_lines(
        modeled={"simulated_shortfall_charge": calculated.simulated_shortfall_charge},
        billed={"simulated_shortfall_charge": float(row["simulated_shortfall_charge"])},
        statement_version=calculated.statement_id,
        tolerance=1e-9,
        confirmed=False,
    )
