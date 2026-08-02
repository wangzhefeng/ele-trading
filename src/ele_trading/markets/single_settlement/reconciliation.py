"""单结算模式的账单对账编排（v5 §11.5）。

结算公式由本插件的 settlement 引擎拥有；这里只把模式已知的分项
词汇与归因提示装配好，调用 ``markets.shared`` 的通用核对引擎。
"""

from __future__ import annotations

from typing import Mapping

from ele_trading.markets.shared import (
    DifferenceCategory,
    ReconciliationReport,
    reconcile_statement_lines,
)

#: 单结算模式已知的账单分项词汇（用于文档化与归属校验）。
KNOWN_LINE_ITEMS: tuple[str, ...] = (
    "energy",
    "contract_difference",
    "monthly_recycle",
    "deviation_penalty",
)


def reconcile_single_settlement_statement(
    *,
    modeled: Mapping[str, float],
    billed: Mapping[str, float],
    statement_version: str,
    tolerance: float,
    confirmed: bool,
    category_hints: Mapping[str, DifferenceCategory] | None = None,
) -> ReconciliationReport:
    """单结算模式账单对账入口。

    参数语义与 ``reconcile_statement_lines`` 一致；``category_hints``
    只能覆盖本模式已知分项，防止把未知分项提前洗白。
    """
    hints = dict(category_hints or {})
    unknown_hints = set(hints) - set(KNOWN_LINE_ITEMS)
    if unknown_hints:
        raise ValueError(
            "category_hints contains lines unknown to single_settlement: "
            + ", ".join(sorted(unknown_hints))
        )
    return reconcile_statement_lines(
        modeled=modeled,
        billed=billed,
        statement_version=statement_version,
        tolerance=tolerance,
        confirmed=confirmed,
        category_hints=hints,
    )
