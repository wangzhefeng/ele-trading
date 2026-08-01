# domain — 当前领域契约

`domain` 保存当前交易链共享的数据类型。现有结构测试要求它不依赖市场、运行、编排、预测、场景或数据接入等上层包；这是当前实现事实，目标依赖关系由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md)重新确认。

## 当前模块

| 模块 | 当前职责 | 状态 |
|---|---|---|
| `contracts.py` | `PositionState`、`MarketForecastBundle`、`OperationalPlan`、`IntradayPlan`、`IntradayAdjustment`、`DRCommitment`、`DecisionTrace` | 活动链使用 |
| `events.py` | `Forecast→Bid→Award→Dispatch→Metering→Settlement` 事件数据类型 | 已定义，活动链未消费 |

`events.py` 当前只被包导出，没有进入 `TradingOrchestrator` 的运行流程。v3 需要决定事件类型是进入编排、保留为数据交换契约，还是删除；在决定前不能把事件骨架描述为已落地架构。
