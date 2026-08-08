# trading — 当前参与方交易编排

本包负责把数据、预测、场景、头寸、日前、日内与结算组合为可运行交易链。当前市场模式由入口注入；单结算是默认用例，双结算仍未形成完整主链。

## 当前模块

| 模块 | 当前职责 |
|---|---|
| `orchestrator.py` | 注入 data provider、forecast provider、场景 builder、`MarketMode`、配置和求解器，运行完整链 |
| `demo_fixtures.py` | 30 天 96 点 `SampleTradingDataProvider`、无前瞻 `WalkForwardSeasonalNaiveProvider` 和 fixture 生成器 |

## 当前流向

```text
data provider → PositionState
forecast provider → 多价格角色 ForecastResult / MarketForecastBundle
scenario builder → ScenarioSet
OperationalPlan → IntradayPlan
MarketMode + SettlementEngine → SettlementReport
```

## 当前事实与缺口

- 编排器请求价格、负荷、风电和光伏预测；市场模式声明日前、实时、价差等所需价格角色；
- 日内以新的 `decision_time` 重新构造预测、状态和场景 vintage，不能复用日前切片；
- 配置和结算由 `MarketMode`/`SettlementEngine` 注入，默认入口使用单结算；
- Position、Forecast、Dispatch、Metering 和 Settlement 已形成事件链；候选 Bid 由注入 builder 构造、经市场模式 capability 验证后产生 BidEvent，AwardEvent 仅由 `MarketAwardReceipt` 构造（未知 bid 显式失败）；真实市场产品与回执适配待 V5-10；
- 执行偏差收紧可选应用于日内重优化并写入 trace；多资源可选返回独立资源级日前计划，尚未进入资源级日内、计量或结算；提供 `BillingStatement` 时结算后自动对账（`confirmed=False` 永不通过）；统一经济验收要求 `InvariantEvidence`，尚无真实账单与影子运行证据。

当前的报价—成交—履约闭环及运行主链接线由 [v5 V5-8、V5-9](../../../docs/策略算法框架详细设计-v5.md#143-v5-8报价成交履约证据链工程链路已完成真实市场接入待-v5-10) 跟踪。样例数据仅用于接口和回归验证，生产数据必须经数据/provider 边界注入。
