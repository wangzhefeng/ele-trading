"""trading — 参与方交易编排层。

沉淀后的职责：``TradingOrchestrator`` 串联 持仓 → 预测 → 场景 →
日前运行 → 日内滚动 → 结算 的完整链路；``demo_fixtures`` 提供蒙西
30 天样例 fixture provider 与生成器。

契约归属：领域契约在 ``domain/``，市场规则在 ``markets/single_settlement/``，
头寸决策在 ``positions/``，运行计划在 ``operations/``，回测在 ``backtest/``。
"""

from ele_trading.trading.demo_fixtures import (
    SampleTradingDataProvider,
    WalkForwardSeasonalNaiveProvider,
)
from ele_trading.trading.orchestrator import TradingOrchestrator

__all__ = [
    "SampleTradingDataProvider",
    "TradingOrchestrator",
    "WalkForwardSeasonalNaiveProvider",
]
