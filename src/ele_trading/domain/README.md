# domain — 当前领域契约

`domain` 保存当前交易链共享的数据类型。结构测试要求它不依赖市场、运行、编排、预测、场景或数据接入等上层包；该已批准边界见 [v3](../../../docs/策略算法框架详细设计-v3.md)，当前契约缺口和下一批次见 [v6 §13、§14](../../../docs/策略算法框架详细设计-v6.md)。

## 当前模块

| 模块 | 当前职责 | 状态 |
|---|---|---|
| `contracts.py` | `BidSubmission`、`MarketAwardReceipt`、`PositionState`、`MarketForecastBundle`、`OperationalPlan`、`IntradayPlan`、`IntradayAdjustment`、`DRCommitment`、`DecisionTrace` | 活动链使用 |
| `events.py` | `Position/Forecast/Bid/Award/Dispatch/Metering/Settlement` 事件数据类型 | Position、Forecast、Bid、Award、Dispatch、Metering、Settlement 均已由编排消费；Bid 经 capability 验证产生，Award 仅由 `MarketAwardReceipt` 构造 |

`DecisionTrace.input_versions` 已由事件前缀派生。`PositionEvent` 表示合同/仓位输入，不得伪装为当前市场 Award；工程闭环已接入主链；当前缺口是真实市场产品规则与回执适配器，由 v6 §9 的 V5-10 继承。
