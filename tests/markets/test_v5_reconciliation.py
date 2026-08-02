"""v5 V5-3（§11.5）：多产品结算账单对账。"""

from __future__ import annotations

import pytest

from ele_trading.markets.shared import DifferenceCategory
from ele_trading.markets.single_settlement.reconciliation import (
    reconcile_single_settlement_statement,
)


MODELED = {
    "energy": 1000.00,
    "contract_difference": 200.00,
    "monthly_recycle": -50.00,
    "deviation_penalty": 30.00,
}


def test_reconciliation_passes_with_only_rounding_differences():
    billed = dict(MODELED, energy=1000.004, deviation_penalty=29.998)
    report = reconcile_single_settlement_statement(
        modeled=MODELED,
        billed=billed,
        statement_version="stmt-2026-07-v1",
        tolerance=0.01,
        confirmed=True,
    )

    assert report.passed
    assert report.confirmed
    assert report.modeled_total == pytest.approx(1180.00)
    assert report.billed_total == pytest.approx(1180.002)
    assert report.differences == ()


def test_unconfirmed_statement_cannot_pass_formal_acceptance():
    report = reconcile_single_settlement_statement(
        modeled=MODELED,
        billed=dict(MODELED),
        statement_version="stmt-draft",
        tolerance=0.01,
        confirmed=False,
    )

    assert not report.passed
    assert not report.confirmed


def test_large_difference_is_unknown_and_fails_without_hint():
    billed = dict(MODELED, energy=900.0)
    report = reconcile_single_settlement_statement(
        modeled=MODELED,
        billed=billed,
        statement_version="stmt-2026-07-v1",
        tolerance=0.01,
        confirmed=True,
    )

    assert not report.passed
    unknown = [d for d in report.differences if d.line_item == "energy"]
    assert len(unknown) == 1
    assert unknown[0].category is DifferenceCategory.UNKNOWN
    assert unknown[0].difference == pytest.approx(-100.0)


def test_category_hints_attribute_differences_and_allow_documented_pass():
    billed = dict(MODELED, monthly_recycle=-40.0)
    report = reconcile_single_settlement_statement(
        modeled=MODELED,
        billed=billed,
        statement_version="stmt-2026-07-v1",
        tolerance=0.01,
        confirmed=True,
        category_hints={"monthly_recycle": DifferenceCategory.RULE},
    )

    assert any(
        d.category is DifferenceCategory.RULE and d.line_item == "monthly_recycle"
        for d in report.differences
    )


def test_missing_line_is_data_difference_and_fails():
    billed = {k: v for k, v in MODELED.items() if k != "deviation_penalty"}
    report = reconcile_single_settlement_statement(
        modeled=MODELED,
        billed=billed,
        statement_version="stmt-2026-07-v1",
        tolerance=0.01,
        confirmed=True,
    )

    assert not report.passed
    missing = [d for d in report.differences if d.line_item == "deviation_penalty"]
    assert len(missing) == 1
    assert missing[0].category is DifferenceCategory.DATA


def test_unknown_difference_cannot_be_hidden_by_netting():
    billed = dict(MODELED, energy=1100.0, contract_difference=100.0)
    report = reconcile_single_settlement_statement(
        modeled=MODELED,
        billed=billed,
        statement_version="stmt-2026-07-v1",
        tolerance=0.01,
        confirmed=True,
    )

    # 总额恰好相等也不能掩盖分项未知差异
    assert report.modeled_total == pytest.approx(report.billed_total)
    assert not report.passed
    assert len(report.differences) == 2


def test_invalid_inputs_raise():
    with pytest.raises(ValueError, match="tolerance"):
        reconcile_single_settlement_statement(
            modeled=MODELED,
            billed=dict(MODELED),
            statement_version="stmt",
            tolerance=-0.1,
            confirmed=True,
        )
    with pytest.raises(ValueError, match="statement_version"):
        reconcile_single_settlement_statement(
            modeled=MODELED,
            billed=dict(MODELED),
            statement_version=" ",
            tolerance=0.01,
            confirmed=True,
        )
    with pytest.raises(ValueError, match="finite"):
        reconcile_single_settlement_statement(
            modeled=dict(MODELED, energy=float("nan")),
            billed=dict(MODELED),
            statement_version="stmt",
            tolerance=0.01,
            confirmed=True,
        )
