# demand_response — 需求响应参与决策

独立策略模块，评估 DR 产品参与的经济性。与蒙西单结算交易主线解耦，
通过 ``MarketConfig`` 的 ``dr_*`` 字段获取经济参数（补偿价、罚金、
最小响应量、窗口、聚合方式等）。

## 模块

| 文件 | 职责 |
|---|---|
| `contracts.py` | `DRDecision` 参与决策结果契约 |
| `allocator.py` | 机会成本估算 + 参与决策评估 |

## 算法

1. **机会成本**（`estimate_arbitrage_opportunity_cost`）：DR 窗口内放弃的计划放电价值 = Σ max(p_net_plan, 0) × 实时价预测 × dt。
2. **参与决策**（`evaluate_dr_participation`）：
   - 响应量 = Σ 窗口内可调容量 × dt
   - 净裕度 = 补偿 − 机会成本 − 违约罚金 − 退化成本
   - 双阈值二值判定：响应量 ≥ 最小响应量 **且** 净裕度 > 最小裕度 → 参与

经济参数全部来自 `configs/markets/single_settlement.yaml`，无硬编码。

## 入口

`app/trading/run_dr.py`：日前计划 → 计算可调容量 → DR 评估。
