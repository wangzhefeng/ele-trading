# positions — 中长期与月度头寸基线

## 当前模块

| 模块 | 当前职责 | 成熟度 |
|---|---|---|
| `contracts.py` | `PositionPlan`、`BidLadder`、`CorridorAdvice` | 活动契约 |
| `mid_long_planner.py` | 价差到覆盖比例的映射、月度分解和实时敞口 | 启发式 |
| `monthly_trader.py` | 阶梯报价、缺口再平衡、无订单簿透明走廊 | 启发式 |
| `mid_long_optimizer.py` | CVaR 约束优化头寸策略（覆盖/预算/换手惩罚/年度总量，v4 P0，可选） | 可选增强 |

当前月度阶梯使用 `uniform` 或 `linear` 简化出清概率，不代表真实订单簿或成交概率模型。无订单簿时只输出透明量价走廊，不构造虚假对手方。

当前头寸层消费共享 `MarketConfig`，并保留透明走廊与可选 CVaR 优化。它尚未成为 MarketMode 可替换的 `PositionPolicy`，也没有真实订单簿/成交校准；报价—成交链与标定要求见 [v6 V5-8～V5-10](../../../docs/策略算法框架详细设计-v6.md)。
