# positions — 中长期与月度头寸基线

## 当前模块

| 模块 | 当前职责 | 成熟度 |
|---|---|---|
| `contracts.py` | `PositionPlan`、`BidLadder`、`CorridorAdvice` | 活动契约 |
| `mid_long_planner.py` | 价差到覆盖比例的映射、月度分解和实时敞口 | 启发式 |
| `monthly_trader.py` | 阶梯报价、缺口再平衡、无订单簿透明走廊 | 启发式 |
| `mid_long_optimizer.py` | CVaR 约束优化头寸策略（覆盖/预算/换手惩罚/年度总量，v4 P0，可选） | 可选增强 |

当前月度阶梯使用 `uniform` 或 `linear` 简化出清概率，不代表真实订单簿或成交概率模型。无订单簿时只输出透明量价走廊，不构造虚假对手方。

当前函数直接接收 `markets.single_settlement.MarketConfig`，因此头寸层尚未与默认市场配置解耦。目标 `PositionPolicy`、参数边界和真实市场标定要求由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#75-市场策略)决定。
