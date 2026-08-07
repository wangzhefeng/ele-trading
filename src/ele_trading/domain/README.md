# domain — 当前领域契约

`domain` 保存当前交易链共享的数据类型。结构测试要求它不依赖市场、运行、编排、预测、场景或数据接入等上层包；该已批准边界见 [v3](../../../docs/策略算法框架详细设计-v3.md)，当前契约缺口和下一批次见 [v5 §13、§14](../../../docs/策略算法框架详细设计-v5.md#13-公共契约与配置影响)。

## 当前模块

| 模块 | 当前职责 | 状态 |
|---|---|---|
| `contracts.py` | `PositionState`、`MarketForecastBundle`、`OperationalPlan`、`IntradayPlan`、`IntradayAdjustment`、`DRCommitment`、`DecisionTrace` | 活动链使用 |
| `events.py` | `Forecast/Award/Dispatch/Metering/Settlement` 事件数据类型和 `BidEvent` | 前五类已由编排和回测消费；BidEvent 尚未由主链产生 |

`DecisionTrace.input_versions` 已由事件前缀派生。当前缺口不是事件骨架，而是交易侧 `BidSubmission`、BidEvent 和 Bid→Award 对应关系；该闭环列为 v5 V5-8。
