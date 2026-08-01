# operations — 日前运行与日内滚动

## 当前模块

| 模块 | 当前职责 | 成熟度 |
|---|---|---|
| `day_ahead_coupled.py` | 日前 BESS 运行、可选联合场景 CVaR、DR 两次求解和决策追踪 | 可运行基线 |
| `intraday_rolling.py` | 冻结执行前缀、剩余窗口重优化、DR 履约约束和物理裁剪回退 | 可运行基线 |

## 当前耦合

- 两个模块直接接收 `markets.single_settlement.MarketConfig`；
- 日前和日内目标使用单结算的电能与合同差价 helper；
- 日前文件同时负责物理模型、场景风险、DR、求解、结果提取和 trace；
- 日内通过复用日前入口完成剩余窗口优化，并在求解失败时生成有记录的物理裁剪计划。

因此，当前 operations 是默认单结算策略的运行层，不是市场无关的 `DispatchStrategy` 实现。共享 BESS 物理、目标策略、DR 和结果提取如何拆分，由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#76-日前与日内运行)决定。
