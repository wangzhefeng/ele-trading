# demand_response — 独立 DR 经济性评估

本包提供事前 DR 参与评估，与默认交易编排中的 DR 联合优化和事后结算是不同职责。

## 当前模块

| 文件 | 当前职责 | 成熟度 |
|---|---|---|
| `contracts.py` | `DRDecision` | 活动结果契约 |
| `allocator.py` | 机会成本估算和二值参与判定 | 启发式 |

## 当前算法

1. `estimate_arbitrage_opportunity_cost()` 根据 DR 窗口内原计划放电、实时价预测和 `dt` 估算放弃套利的价值。
2. `evaluate_dr_participation()` 计算响应量、补偿、机会成本、违约罚金、退化成本和净裕度，再按最小响应量与最小裕度做二值判定。

经济参数当前来自 `markets.single_settlement.MarketConfig`，因此独立 allocator 仍与默认单结算配置耦合。

## 与主链 DR 的区别

- 独立评估入口：`app/trading/run_dr.py`；
- 日前联合优化：`operations/day_ahead_coupled.py`；
- 日内履约：`operations/intraday_rolling.py`；
- 事后补偿与罚金：`markets/single_settlement/settlement.compute_dr_settlement()`。

v3 需要决定 DR 产品契约、机会成本、联合优化和结算是否共享统一策略接口，见[市场策略设计](../../../docs/策略算法框架详细设计-v3.md#75-市场策略)。
