"""markets 共享工具：跨结算模式通用的结算辅助函数。

当前收录 ``aggregate_to_settle_periods``（结算时段能量守恒聚合）。
统一采用单结算插件的现役实现（含 ndim 校验）；归档 v1 双结算版本
（带 settle_periods == n 快速路径）语义等价，已随归档删除。

v5 V5-3（§11.5）新增跨模式共享的账单对账契约与归因工具：
``DifferenceCategory`` / ``ReconciliationDifference`` /
``ReconciliationReport`` / ``reconcile_statement_lines``。
结算公式仍由 MarketMode 插件拥有；这里只负责"建模分项 vs 账单分项"
的差异核对与分类，不知道任何模式专属公式。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import numpy as np


class DifferenceCategory(str, Enum):
    """账单差异归因类别（v5 §11.5）。"""

    RULE = "rule"
    PARAMETER = "parameter"
    DATA = "data"
    METERING = "metering"
    ROUNDING = "rounding"
    TIME_BOUNDARY = "time_boundary"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReconciliationDifference:
    """单个账单分项的差异记录。``difference = billed - modeled``。"""

    line_item: str
    category: DifferenceCategory
    modeled_amount: float
    billed_amount: float
    difference: float
    detail: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """对账结果：confirmed=False 的账单不得通过正式验收（v5 §11.5）。"""

    statement_version: str
    modeled_total: float
    billed_total: float
    differences: tuple[ReconciliationDifference, ...]
    passed: bool
    confirmed: bool


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


def _finite_lines(
    lines: Mapping[str, float],
    field_name: str,
) -> dict[str, float]:
    if not isinstance(lines, Mapping) or not lines:
        raise ValueError(f"{field_name} must be a non-empty mapping")
    normalized: dict[str, float] = {}
    for name, amount in lines.items():
        name = _non_empty(name, f"{field_name} line item")
        value = float(amount)
        if not np.isfinite(value):
            raise ValueError(f"{field_name}[{name!r}] must be finite")
        normalized[name] = value
    return normalized


def reconcile_statement_lines(
    *,
    modeled: Mapping[str, float],
    billed: Mapping[str, float],
    statement_version: str,
    tolerance: float,
    confirmed: bool,
    category_hints: Mapping[str, DifferenceCategory] | None = None,
) -> ReconciliationReport:
    """逐分项核对建模金额与账单金额。

    语义：
    - ``|billed - modeled| <= tolerance`` 的差异视为舍入吸收，不记录；
    - 一侧缺失的分项记为 ``DATA`` 差异；
    - 超容差且无归因提示的差异记为 ``UNKNOWN``——未知差异不能被
      其他分项抵消后隐藏（v5 §11.5），因此 ``passed`` 要求逐分项
      全部干净，与总额是否恰好相等无关；
    - ``confirmed=False`` 时 ``passed`` 恒为 False（草稿账单不能
      通过正式验收）。
    """
    statement_version = _non_empty(statement_version, "statement_version")
    tolerance = float(tolerance)
    if not np.isfinite(tolerance) or tolerance < 0.0:
        raise ValueError("tolerance must be finite and non-negative")
    modeled_lines = _finite_lines(modeled, "modeled")
    billed_lines = _finite_lines(billed, "billed")
    hints = dict(category_hints or {})
    for name, category in hints.items():
        _non_empty(name, "category_hints line item")
        if not isinstance(category, DifferenceCategory):
            raise ValueError("category_hints values must be DifferenceCategory")

    differences: list[ReconciliationDifference] = []
    for line_item in sorted(set(modeled_lines) | set(billed_lines)):
        in_modeled = line_item in modeled_lines
        in_billed = line_item in billed_lines
        modeled_amount = modeled_lines.get(line_item, 0.0)
        billed_amount = billed_lines.get(line_item, 0.0)
        difference = billed_amount - modeled_amount
        if not in_modeled or not in_billed:
            differences.append(
                ReconciliationDifference(
                    line_item=line_item,
                    category=DifferenceCategory.DATA,
                    modeled_amount=modeled_amount,
                    billed_amount=billed_amount,
                    difference=difference,
                    detail=(
                        "missing in billed statement"
                        if not in_billed
                        else "missing in modeled statement"
                    ),
                )
            )
            continue
        if abs(difference) <= tolerance:
            continue
        category = hints.get(line_item, DifferenceCategory.UNKNOWN)
        differences.append(
            ReconciliationDifference(
                line_item=line_item,
                category=category,
                modeled_amount=modeled_amount,
                billed_amount=billed_amount,
                difference=difference,
                detail=(
                    "attributed by category hint"
                    if line_item in hints
                    else "unexplained difference above tolerance"
                ),
            )
        )

    return ReconciliationReport(
        statement_version=statement_version,
        modeled_total=float(sum(modeled_lines.values())),
        billed_total=float(sum(billed_lines.values())),
        differences=tuple(differences),
        passed=bool(confirmed) and not differences,
        confirmed=bool(confirmed),
    )


def aggregate_to_settle_periods(
    quantity: np.ndarray,
    settle_periods: int,
) -> np.ndarray:
    """Aggregate interval energy while preserving the total quantity."""
    values = np.asarray(quantity, dtype=float)
    if (
        settle_periods <= 0
        or values.ndim != 1
        or len(values) % settle_periods != 0
    ):
        raise ValueError(
            "settle_periods must be a positive divisor of the horizon"
        )
    return values.reshape(settle_periods, -1).sum(axis=1)
