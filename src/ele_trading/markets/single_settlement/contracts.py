"""单结算模式的结算报告契约与配置兼容出口。

v3 M4（D-002）：六个配置子对象与组合 ``MarketConfig`` 已上移到
``ele_trading.markets.sections``（主链共享配置词汇）；本模块
re-export 以保持插件自包含与既有引用路径，并单独持有单结算
专属的 ``SettlementReport``。
"""

from __future__ import annotations

from dataclasses import dataclass

from ele_trading.domain.contracts import DecisionTrace
from ele_trading.markets.sections import (  # noqa: F401  (re-export)
    CURRENT_SCHEMA_VERSION,
    BessSection,
    DrSection,
    MarketConfig,
    MarketSection,
    MonthlySection,
    ScenarioSection,
    SolverSection,
)


@dataclass(slots=True)
class SettlementReport:
    """Itemized active single-settlement result."""

    energy_cost: float
    contract_difference: float
    long_recovery: float
    dr_adjustment: float
    degradation_cost: float
    execution_adjustment: float
    total_cost: float
    baseline_cost: float
    delta_cost: float
    trace: DecisionTrace | None = None
