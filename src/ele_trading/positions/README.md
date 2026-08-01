# positions — 中长期/月度头寸决策层

## 模块

| 模块 | 职责 |
|------|------|
| `contracts.py` | `PositionPlan`、`BidLadder`、`CorridorAdvice` 头寸契约 |
| `mid_long_planner.py` | 中长期覆盖结构与实时敞口（`plan_mid_long_position`） |
| `monthly_trader.py` | 月度集中竞价阶梯申报（`build_bid_ladder`）、缺口再平衡（`rebalance_position_gap`）、无订单簿时的透明量价走廊（`build_position_corridor`） |

市场规则参数经 `markets.single_settlement.MarketConfig` 注入，不硬编码。
