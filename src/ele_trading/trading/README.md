# trading — 当前参与方交易编排

本包是默认单结算链的编排落点，负责把数据、预测、场景、日前、日内和结算组合成一个可运行流程。

## 当前模块

| 模块 | 当前职责 |
|---|---|
| `orchestrator.py` | 注入 data provider、forecast provider、场景 builder、配置和求解器并运行完整链 |
| `demo_fixtures.py` | 30 天 96 点 `SampleTradingDataProvider`、无前瞻 `WalkForwardSeasonalNaiveProvider` 和 fixture 生成器 |

## 当前流向

```text
data provider → PositionState
forecast provider → price/load/wind_power/pv_power ForecastResult
scenario builder → ScenarioSet
OperationalPlan → IntradayPlan
single-settlement → SettlementReport
```

## 当前固定假设

- 编排器固定请求价格、负荷、风电和光伏四类预测；
- 当前 frequency 固定为 `15min`；
- 配置和结算报告固定使用单结算实现；
- 日前价格作为运行信号，实际结算使用事后实际价格；
- 实际负荷和价格只在完成决策后进入结算。

这些是当前实现事实，不是 v3 永久架构。市场策略注入、频率契约和事件编排由 [v3 设计](../../../docs/策略算法框架详细设计-v3.md#77-编排与结算)决定。

## 活动入口与归档

活动入口为 `app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,backtest}.py` 和 `run_pipeline.py`。样例数据只用于接口和回归验证，生产数据必须经数据/provider 边界注入。

旧双结算报价、日前和回测实现只存在于 git 历史；当前唯一双结算代码位于 `markets/dual_settlement/`，但未接本包编排。
